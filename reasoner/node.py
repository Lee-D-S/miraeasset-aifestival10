"""Canonical single-node Reasoner pipeline for the shared LangGraph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Callable

from integration.rate_limit import is_rate_limit_error, rate_limit_event
from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.adapters.retriever import adapt_retriever_bundle
from reasoner.agents.answer import AnswerWriter
from reasoner.agents.comparison import compare_facts
from reasoner.agents.event_linker import link_events
from reasoner.agents.fact_extraction import extract_facts
from reasoner.agents.fact_recovery import FactRecovery
from reasoner.contracts import ReasonerFact, ReasonerIntent, ReasonerResult
from reasoner.deterministic.calculation_planner import (
    SUPPORTED_OPERATIONS,
    build_calculation_plan,
    validate_analysis_plan,
)
from reasoner.deterministic.calculations import calculate_facts, execute_analysis_plan
from reasoner.deterministic.normalization import normalize_facts
from reasoner.grounding import matching_facts, strict_grounding_enabled
from reasoner.state import ReasonerNodeOutput
from reasoner.validation import validate_reasoner_result


_EVENT_QUESTION_TYPES = frozenset({"event", "exists", "event_link", "change", "contract", "correction"})
_EVENT_METRICS = frozenset({"supply_contract", "contract_termination"})
_FINANCIAL_POSITION_FACT_METRICS = frozenset({"assets", "liabilities", "equity", "ratio"})


def _question_type(intent: ReasonerIntent) -> str:
    return str(intent.question_type or intent.intent or "").strip().lower()


def _needs_event_linking(intent: ReasonerIntent) -> bool:
    return (
        _question_type(intent) in _EVENT_QUESTION_TYPES
        or intent.metric in _EVENT_METRICS
        or intent.correction_mode == "include_chain"
    )


def _documents_for_citations(documents: list[Any], used_ids: set[str]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    for document in documents:
        if document.id not in used_ids:
            continue
        citations.append({
            "document_id": document.id,
            "source": document.source,
            "evidence": document.text,
            "metadata": dict(document.metadata),
        })
    return citations


def _build_citations(
    documents: list[Any],
    facts: list[ReasonerFact],
    calculations: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    used_ids = {fact.document_id for fact in facts if fact.document_id}
    for item in calculations + comparisons:
        used_ids.update(str(value) for value in item.get("evidence_ids", []) if value)
    for event in events:
        used_ids.update(str(value) for value in event.get("source_ids", []) if value)

    evidence_by_id: dict[str, list[str]] = {}
    for fact in facts:
        if fact.document_id and fact.evidence:
            evidence_by_id.setdefault(fact.document_id, []).append(fact.evidence)
    for document in documents:
        for span in document.evidence_spans:
            value = span.get("text", span.get("content", span.get("evidence", "")))
            if str(value).strip():
                evidence_by_id.setdefault(document.id, []).append(str(value).strip())

    citations: list[dict[str, Any]] = []
    for item in _documents_for_citations(documents, used_ids):
        document_id = item["document_id"]
        snippets = list(dict.fromkeys(evidence_by_id.get(document_id, [])))
        if snippets:
            item["evidence"] = "\n".join(snippets)
        citations.append(item)
    return citations


def _context_from_result(result: ReasonerResult) -> str:
    parts: list[str] = []
    for citation in result.citations:
        parts.append(
            f"[출처: {citation.get('source', '')}][문서ID: {citation.get('document_id', '')}]\n"
            f"{citation.get('evidence', '')}"
        )
    for calculation in result.calculations:
        parts.append(
            f"[계산] operation={calculation.get('operation', '')}; "
            f"formula={calculation.get('formula', '')}; result={calculation.get('result', '')}; "
            f"unit={calculation.get('unit', '')}; evidence_ids={calculation.get('evidence_ids', [])}"
        )
    for comparison in result.comparison_results:
        parts.append(
            f"[비교] status={comparison.get('status', '')}; "
            f"results={comparison.get('results', [])}; evidence_ids={comparison.get('evidence_ids', [])}"
        )
    for event in result.linked_events:
        parts.append(
            f"[이벤트] relation={event.get('relation', '')}; "
            f"source_ids={event.get('source_ids', [])}; status={event.get('status', '')}"
        )
    return "\n\n".join(parts)


def _successful_analysis(
    question_type: str,
    intent: ReasonerIntent,
    facts: list[ReasonerFact],
    calculations: list[dict[str, Any]],
    comparisons: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> bool:
    if question_type in {"compare", "comparison"}:
        return any(item.get("status") == "ok" for item in comparisons)
    if question_type in {"calculation", "calc"}:
        return any(item.get("status") == "ok" for item in calculations)
    if not (facts or events):
        return False
    if question_type in {"lookup", "text", "exists", "event"} and facts:
        if strict_grounding_enabled() and intent.metric and not matching_facts(facts, intent) and not events:
            return False
        requested = str(getattr(intent, "metric", "") or "").strip().lower()
        if requested and not any(_fact_matches_requested_metric(f, requested) for f in facts) and not events:
            return False
    if question_type in {"event", "exists"} and events:
        return any(event.get("status") in {None, "linked", "ok"} for event in events)
    return True


def _fact_matches_requested_metric(fact: ReasonerFact, requested: str) -> bool:
    """Match Interpreter retrieval metrics to their Reasoner fact subtypes."""

    if requested == "total_assets":
        # Interpreter uses one retrieval key for the financial-position section;
        # Reasoner splits its labels into assets/liabilities/equity/ratio facts.
        return fact.metric in _FINANCIAL_POSITION_FACT_METRICS
    return requested in fact.metric.lower() or requested in fact.label.lower()


def _plan_sub_intent(intent: ReasonerIntent, requirement: Mapping[str, Any]) -> ReasonerIntent:
    source = intent.to_dict()
    source["query_plan"] = []
    source["analysis_plan"] = {}
    source["metric"] = requirement.get("metric")
    source["calculation"] = {}
    source["time"] = {"years": list(requirement.get("periods", []))}
    source["manifest_filter"] = dict(requirement.get("manifest_filter", {}))
    return adapt_interpreter_intent(source, question=intent.question)


def _plan_documents(retriever_result: Any, requirement_id: str, fallback: list[Any]) -> list[Any]:
    if not isinstance(retriever_result, Mapping):
        return fallback
    for item in retriever_result.get("subresults", []):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("requirement_id") or item.get("subquery_id")) != requirement_id:
            continue
        bundle = adapt_retriever_bundle(item)
        return bundle.effective_documents()
    return fallback


def _execute_analysis_plan_reasoner(
    *,
    question: str,
    intent: ReasonerIntent,
    retriever_result: Any,
    writer: AnswerWriter,
    cache: Any | None = None,
    recovery: FactRecovery | None = None,
) -> ReasonerResult:
    try:
        plan = validate_analysis_plan(intent.analysis_plan)
    except (TypeError, ValueError) as error:
        return ReasonerResult(
            status="insufficient_evidence",
            warnings=[f"analysis_plan_invalid: {type(error).__name__}"],
            trace=["reasoner_start", "analysis_plan_invalid"],
        )

    bundle = adapt_retriever_bundle(retriever_result)
    documents = bundle.effective_documents()
    warnings = list(intent.warnings)
    warnings.extend(bundle.retrieval_trace)
    facts: list[ReasonerFact] = []
    seen_facts: set[tuple[str, str, str, str, str, str]] = set()
    for requirement in plan["requirements"]:
        requirement_id = str(requirement["id"])
        sub_documents = _plan_documents(retriever_result, requirement_id, documents)
        sub_intent = _plan_sub_intent(intent, requirement)
        extracted = extract_facts(sub_documents, sub_intent, cache=cache)
        if recovery is not None:
            extracted = recovery.recover(sub_documents, sub_intent, extracted)
        normalized, normalization_warnings = normalize_facts(extracted, sub_intent)
        warnings.extend(f"{requirement_id}: {warning}" for warning in normalization_warnings)
        for fact in normalized:
            key = (
                fact.document_id,
                fact.metric,
                fact.label,
                str(fact.period),
                str(fact.company),
                str(fact.normalized_value),
            )
            if key not in seen_facts:
                seen_facts.add(key)
                facts.append(fact)
    execution = execute_analysis_plan(plan, facts, intent=intent)
    warnings.extend(execution["warnings"])
    derived_facts = [ReasonerFact.from_dict(value) for value in execution["derived_facts"]]
    all_facts = [*facts, *derived_facts]
    calculations = list(execution["calculations"])
    comparisons = list(execution["comparisons"])
    citations = _build_citations(documents, all_facts, calculations, comparisons, [])
    analysis_success = bool(execution["success"])
    result = ReasonerResult(
        status="success" if analysis_success else "insufficient_evidence",
        facts=[fact.to_dict() for fact in all_facts],
        calculations=calculations,
        comparison_results=comparisons,
        citations=citations,
        warnings=warnings,
        trace=[
            "reasoner_start",
            "analysis_plan_executed",
            f"steps={len(plan['steps'])}",
            f"requirements={len(plan['requirements'])}",
            f"facts={len(all_facts)}",
        ],
        provider_status={},
    )
    valid, validation_warnings = validate_reasoner_result(result, intent)
    if not valid:
        result = replace(
            result,
            status="insufficient_evidence",
            warnings=[*result.warnings, *validation_warnings],
            trace=[*result.trace, "reasoner_validation_failed"],
        )
        analysis_success = False

    if analysis_success:
        try:
            answer, answer_mode = writer.write(
                question=question,
                intent=intent,
                facts=all_facts,
                calculations=calculations,
                comparisons=comparisons,
                events=[],
                citations=citations,
                warnings=warnings,
            )
        except Exception as error:  # noqa: BLE001 - injected provider boundary
            answer = writer.deterministic(
                intent=intent,
                facts=all_facts,
                calculations=calculations,
                comparisons=comparisons,
                events=[],
                citations=citations,
                warnings=[*warnings, f"answer_provider_error: {type(error).__name__}"],
            )
            answer_mode = "deterministic_fallback"
    else:
        answer = writer.deterministic(
            intent=intent,
            facts=all_facts,
            calculations=calculations,
            comparisons=comparisons,
            events=[],
            citations=citations,
            warnings=warnings,
        )
        answer_mode = "deterministic_fallback"
    return replace(result, answer=str(answer), trace=[*result.trace, f"answer_mode={answer_mode}"])


def _execute_reasoner(
    *,
    question: str,
    intent: ReasonerIntent,
    retriever_result: Any,
    writer: AnswerWriter,
    write_answer: bool = True,
    cache: Any | None = None,
    recovery: FactRecovery | None = None,
) -> ReasonerResult:
    if not intent.is_processable:
        return ReasonerResult(
            status=intent.route,
            warnings=list(intent.warnings),
            trace=[f"route={intent.route}", "reasoner_skipped"],
        )

    if intent.analysis_plan.get("status") == "ready":
        return _execute_analysis_plan_reasoner(
            question=question,
            intent=intent,
            retriever_result=retriever_result,
            writer=writer,
            cache=cache,
            recovery=recovery,
        )

    if len(intent.query_plan) > 1:
        return _execute_multi_query_reasoner(
            question=question,
            intent=intent,
            retriever_result=retriever_result,
            writer=writer,
            cache=cache,
            recovery=recovery,
        )

    bundle = adapt_retriever_bundle(retriever_result)
    documents = bundle.effective_documents()
    warnings = list(intent.warnings)
    warnings.extend(bundle.retrieval_trace)
    if not documents:
        warnings.append("구조화된 Retriever 근거 문서가 없습니다.")
    for document in documents:
        file_format = str(document.metadata.get("file_format", "")).lower()
        has_span_text = any(
            str(span.get("text", span.get("content", span.get("evidence", "")))).strip()
            for span in document.evidence_spans
        )
        if "pdf" in file_format and not document.text.strip() and not has_span_text:
            warnings.append(f"{document.id}: pdf_text_required")

    raw_facts = extract_facts(documents, intent, cache=cache)
    if recovery is not None:
        raw_facts = recovery.recover(documents, intent, raw_facts)
    facts, normalization_warnings = normalize_facts(raw_facts, intent)
    warnings.extend(normalization_warnings)

    events = link_events(documents, intent) if _needs_event_linking(intent) else []
    calculations: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    question_type = _question_type(intent)
    if question_type in {"compare", "comparison"}:
        comparisons.append(compare_facts(facts, intent))
    elif question_type in {"calculation", "calc"}:
        plan = build_calculation_plan(intent)
        if not plan:
            calculations.append({
                "status": "missing_calculation_plan",
                "operation": "",
                "error": "Interpreter calculation.operation이 없습니다.",
            })
            warnings.append("계산 연산이 Interpreter Intent에 지정되지 않았습니다.")
        elif plan.get("operation") not in SUPPORTED_OPERATIONS:
            calculations.append({
                "status": "unsupported",
                "operation": str(plan.get("operation", "")),
                "error": f"지원하지 않는 계산 연산입니다: {plan.get('operation', '')}",
            })
            warnings.append(str(calculations[-1]["error"]))
        else:
            calculations.append(calculate_facts(facts, intent, operation=str(plan["operation"])))
            if calculations[-1].get("error"):
                warnings.append(str(calculations[-1]["error"]))

    citations = _build_citations(documents, facts, calculations, comparisons, events)
    analysis_success = _successful_analysis(question_type, intent, facts, calculations, comparisons, events)
    result_status = "success" if analysis_success else "insufficient_evidence"
    result = ReasonerResult(
        status=result_status,
        facts=[fact.to_dict() for fact in facts],
        calculations=calculations,
        comparison_results=comparisons,
        linked_events=events,
        citations=citations,
        warnings=warnings,
        trace=[
            "reasoner_start",
            f"question_type={question_type or 'unknown'}",
            f"documents={len(documents)}",
            f"facts={len(facts)}",
            f"events={len(events)}",
            f"calculations={len(calculations)}",
            f"comparisons={len(comparisons)}",
        ],
        provider_status={},
    )

    valid, validation_warnings = validate_reasoner_result(result, intent)
    if not valid:
        result = replace(
            result,
            status="insufficient_evidence",
            warnings=[*result.warnings, *validation_warnings],
            trace=[*result.trace, "reasoner_validation_failed"],
        )
        analysis_success = False

    if not write_answer:
        return result

    if analysis_success:
        try:
            answer, answer_mode = writer.write(
                question=question,
                intent=intent,
                facts=facts,
                calculations=calculations,
                comparisons=comparisons,
                events=events,
                citations=citations,
                warnings=warnings,
            )
        except Exception as error:  # noqa: BLE001 - injected provider boundary
            answer = writer.deterministic(
                intent=intent,
                facts=facts,
                calculations=calculations,
                comparisons=comparisons,
                events=events,
                citations=citations,
                warnings=[*warnings, f"answer_provider_error: {type(error).__name__}"],
            )
            answer_mode = "deterministic_fallback"
            if is_rate_limit_error(error):
                result = replace(
                    result,
                    warnings=[*result.warnings, "provider_rate_limited: answer_generation"],
                    trace=[*result.trace, "provider_rate_limited_deterministic_fallback"],
                    provider_status=rate_limit_event(
                        error,
                        operation="answer_generation",
                        client=writer.client,
                    ),
                )
                answer_mode = "deterministic_fallback_provider_rate_limited"
            else:
                result.warnings.append(f"answer_provider_error: {type(error).__name__}: {error}")
    else:
        answer = writer.deterministic(
            intent=intent,
            facts=facts,
            calculations=calculations,
            comparisons=comparisons,
            events=events,
            citations=citations,
            warnings=warnings,
        )
        answer_mode = "deterministic_fallback"

    return replace(result, answer=str(answer), trace=[*result.trace, f"answer_mode={answer_mode}"])


def _subquery_intent(intent: ReasonerIntent, item: Mapping[str, Any]) -> ReasonerIntent:
    source = intent.to_dict()
    source["query_plan"] = []
    source["route"] = "ok"
    for key in ("metric", "question_type", "calculation", "basis", "time", "manifest_filter"):
        if key in item:
            source[key] = item[key]
    return adapt_interpreter_intent(source, question=intent.question)


def _execute_multi_query_reasoner(
    *,
    question: str,
    intent: ReasonerIntent,
    retriever_result: Any,
    writer: AnswerWriter,
    cache: Any | None = None,
    recovery: FactRecovery | None = None,
) -> ReasonerResult:
    raw_subresults = retriever_result.get("subresults", []) if isinstance(retriever_result, Mapping) else []
    by_id = {
        str(item.get("subquery_id")): item
        for item in raw_subresults
        if isinstance(item, Mapping) and item.get("subquery_id")
    }
    subresults: list[dict[str, Any]] = []
    facts: list[ReasonerFact] = []
    calculations: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings = list(intent.warnings)
    for item in intent.query_plan:
        subquery_id = str(item.get("subquery_id") or f"subquery-{len(subresults) + 1}")
        sub_intent = _subquery_intent(intent, item)
        subresult = _execute_reasoner(
            question=question,
            intent=sub_intent,
            retriever_result=by_id.get(subquery_id, {}),
            writer=writer,
            write_answer=False,
            cache=cache,
            recovery=recovery,
        )
        sub_dict = subresult.to_dict()
        sub_dict["subquery_id"] = subquery_id
        subresults.append(sub_dict)
        facts.extend(ReasonerFact.from_dict(value) for value in subresult.facts)
        calculations.extend({**value, "subquery_id": subquery_id} for value in subresult.calculations)
        comparisons.extend({**value, "subquery_id": subquery_id} for value in subresult.comparison_results)
        events.extend({**value, "subquery_id": subquery_id} for value in subresult.linked_events)
        warnings.extend(f"{subquery_id}: {value}" for value in subresult.warnings)

    bundle = adapt_retriever_bundle(retriever_result)
    citations = _build_citations(bundle.effective_documents(), facts, calculations, comparisons, events)
    success_count = sum(item.get("status") == "success" for item in subresults)
    result_status = (
        "success"
        if success_count == len(subresults) and subresults
        else "partial_success"
        if success_count
        else "insufficient_evidence"
    )
    result = ReasonerResult(
        status=result_status,
        facts=[fact.to_dict() for fact in facts],
        calculations=calculations,
        comparison_results=comparisons,
        linked_events=events,
        citations=citations,
        warnings=warnings,
        subresults=subresults,
        trace=[
            "reasoner_start",
            "question_type=multi_query",
            f"subqueries={len(subresults)}",
            f"successful_subqueries={success_count}",
            f"facts={len(facts)}",
        ],
    )
    valid, validation_warnings = validate_reasoner_result(result, intent)
    if not valid:
        result = replace(
            result,
            status="insufficient_evidence",
            warnings=[*result.warnings, *validation_warnings],
            trace=[*result.trace, "reasoner_validation_failed"],
        )
        result_status = "insufficient_evidence"

    analysis_success = result_status in {"success", "partial_success"}
    if analysis_success:
        try:
            answer, answer_mode = writer.write(
                question=question,
                intent=intent,
                facts=facts,
                calculations=calculations,
                comparisons=comparisons,
                events=events,
                citations=citations,
                warnings=warnings,
            )
        except Exception as error:  # noqa: BLE001 - injected provider boundary
            answer = writer.deterministic(
                intent=intent,
                facts=facts,
                calculations=calculations,
                comparisons=comparisons,
                events=events,
                citations=citations,
                warnings=[*warnings, f"answer_provider_error: {type(error).__name__}"],
            )
            answer_mode = "deterministic_fallback"
            if is_rate_limit_error(error):
                result = replace(
                    result,
                    warnings=[*result.warnings, "provider_rate_limited: answer_generation"],
                    provider_status=rate_limit_event(error, operation="answer_generation", client=writer.client),
                )
    else:
        answer = writer.deterministic(
            intent=intent,
            facts=facts,
            calculations=calculations,
            comparisons=comparisons,
            events=events,
            citations=citations,
            warnings=warnings,
        )
        answer_mode = "deterministic_fallback"
    return replace(result, answer=str(answer), trace=[*result.trace, f"answer_mode={answer_mode}"])


def _intent_from_state(state: Mapping[str, Any]) -> ReasonerIntent:
    raw_intent = state.get("intent", {})
    question = str(state.get("question", ""))
    state_route = state.get("route")
    if isinstance(raw_intent, ReasonerIntent):
        if state_route is not None and str(state_route) != raw_intent.route:
            return adapt_interpreter_intent({**raw_intent.to_dict(), "route": state_route}, question=question or raw_intent.question)
        return raw_intent
    source = dict(raw_intent) if isinstance(raw_intent, Mapping) else {}
    if isinstance(state.get("analysis_plan"), Mapping) and state["analysis_plan"].get("status") == "ready":
        source["analysis_plan"] = dict(state["analysis_plan"])
    if state_route is not None:
        source["route"] = state_route
    question = question or str(source.get("raw_question", source.get("question", "")))
    return adapt_interpreter_intent(source, question=question)


def _message(answer: str) -> Any:
    try:
        from langchain_core.messages import AIMessage
    except ImportError:
        return {"role": "assistant", "content": answer}
    return AIMessage(content=answer)


def build_reasoner_node(
    *,
    answer_client: Any | None = None,
    answer_writer: AnswerWriter | None = None,
    cache: Any | None = None,
) -> Callable[[Mapping[str, Any]], ReasonerNodeOutput]:
    """Build the single LangGraph-compatible Reasoner node.

    The returned callable reads a structural subset of the shared state and
    returns only a partial update.  It never performs retrieval or Validator
    validation and never mutates the input mapping.
    """

    if answer_client is not None and answer_writer is not None:
        raise ValueError("answer_client와 answer_writer를 동시에 지정할 수 없습니다.")
    writer = answer_writer or AnswerWriter(answer_client)

    def reasoner_node(state: Mapping[str, Any]) -> ReasonerNodeOutput:
        intent = _intent_from_state(state)
        question = str(state.get("question", intent.question))
        recovery = FactRecovery(writer.client)
        try:
            result = _execute_reasoner(
                question=question,
                intent=intent,
                retriever_result=state.get("retriever_result", {}),
                writer=writer,
                cache=cache,
                recovery=recovery,
            )
        except Exception as error:  # noqa: BLE001 - node boundary must reach Validator
            result = ReasonerResult(
                status="error",
                answer="Reasoner 처리 중 근거를 구성할 수 없습니다.",
                warnings=[f"reasoner_error: {type(error).__name__}"],
                trace=["reasoner_failed"],
            )

        result = replace(result, trace=[*result.trace, *recovery.trace])
        update: ReasonerNodeOutput = {"reasoner_result": result.to_dict()}
        if not intent.is_processable:
            return update
        update["facts"] = list(result.facts)

        current_retry = state.get("gen_retry_num", 0)
        try:
            next_retry = int(current_retry) + 1
        except (TypeError, ValueError):
            next_retry = 1
        update.update({
            "answer": result.answer,
            "context": _context_from_result(result),
            "messages": [_message(result.answer)],
            "gen_retry_num": next_retry,
        })
        return update

    return reasoner_node


__all__ = ["build_reasoner_node"]
