"""Canonical single-node Stage3 pipeline for the shared LangGraph."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from typing import Any, Callable

from integration.rate_limit import is_rate_limit_error, rate_limit_event
from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.answer import AnswerWriter
from stage3.agents.comparison import compare_facts
from stage3.agents.event_linker import link_events
from stage3.agents.fact_extraction import extract_facts
from stage3.contracts import Stage3Fact, Stage3Intent, Stage3Result
from stage3.deterministic.calculation_planner import SUPPORTED_OPERATIONS, build_calculation_plan
from stage3.deterministic.calculations import calculate_facts
from stage3.deterministic.normalization import normalize_facts
from stage3.grounding import matching_facts, strict_grounding_enabled
from stage3.state import Stage3NodeOutput
from stage3.validation import validate_stage3_result


_EVENT_QUESTION_TYPES = frozenset({"event", "exists", "event_link", "change", "contract", "correction"})
_EVENT_METRICS = frozenset({"supply_contract", "contract_termination"})
_FINANCIAL_POSITION_FACT_METRICS = frozenset({"assets", "liabilities", "equity", "ratio"})


def _question_type(intent: Stage3Intent) -> str:
    return str(intent.question_type or intent.intent or "").strip().lower()


def _needs_event_linking(intent: Stage3Intent) -> bool:
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
    facts: list[Stage3Fact],
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


def _context_from_result(result: Stage3Result) -> str:
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
    intent: Stage3Intent,
    facts: list[Stage3Fact],
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


def _fact_matches_requested_metric(fact: Stage3Fact, requested: str) -> bool:
    """Match Stage1 retrieval metrics to their Stage3 fact subtypes."""

    if requested == "total_assets":
        # Stage1 uses one retrieval key for the financial-position section;
        # Stage3 splits its labels into assets/liabilities/equity/ratio facts.
        return fact.metric in _FINANCIAL_POSITION_FACT_METRICS
    return requested in fact.metric.lower() or requested in fact.label.lower()


def _execute_stage3(
    *,
    question: str,
    intent: Stage3Intent,
    stage2_result: Any,
    writer: AnswerWriter,
    write_answer: bool = True,
    cache: Any | None = None,
) -> Stage3Result:
    if not intent.is_processable:
        return Stage3Result(
            status=intent.route,
            warnings=list(intent.warnings),
            trace=[f"route={intent.route}", "stage3_skipped"],
        )

    if len(intent.query_plan) > 1:
        return _execute_multi_query_stage3(
            question=question,
            intent=intent,
            stage2_result=stage2_result,
            writer=writer,
            cache=cache,
        )

    bundle = adapt_stage2_bundle(stage2_result)
    documents = bundle.effective_documents()
    warnings = list(intent.warnings)
    warnings.extend(bundle.retrieval_trace)
    if not documents:
        warnings.append("구조화된 Stage2 근거 문서가 없습니다.")
    for document in documents:
        file_format = str(document.metadata.get("file_format", "")).lower()
        has_span_text = any(
            str(span.get("text", span.get("content", span.get("evidence", "")))).strip()
            for span in document.evidence_spans
        )
        if "pdf" in file_format and not document.text.strip() and not has_span_text:
            warnings.append(f"{document.id}: pdf_text_required")

    raw_facts = extract_facts(documents, intent, cache=cache)
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
                "error": "Stage1 calculation.operation이 없습니다.",
            })
            warnings.append("계산 연산이 Stage1 Intent에 지정되지 않았습니다.")
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
    result = Stage3Result(
        status=result_status,
        facts=[fact.to_dict() for fact in facts],
        calculations=calculations,
        comparison_results=comparisons,
        linked_events=events,
        citations=citations,
        warnings=warnings,
        trace=[
            "stage3_start",
            f"question_type={question_type or 'unknown'}",
            f"documents={len(documents)}",
            f"facts={len(facts)}",
            f"events={len(events)}",
            f"calculations={len(calculations)}",
            f"comparisons={len(comparisons)}",
        ],
        provider_status={},
    )

    valid, validation_warnings = validate_stage3_result(result, intent)
    if not valid:
        result = replace(
            result,
            status="insufficient_evidence",
            warnings=[*result.warnings, *validation_warnings],
            trace=[*result.trace, "stage3_validation_failed"],
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


def _subquery_intent(intent: Stage3Intent, item: Mapping[str, Any]) -> Stage3Intent:
    source = intent.to_dict()
    source["query_plan"] = []
    source["route"] = "ok"
    for key in ("metric", "question_type", "calculation", "basis", "time", "manifest_filter"):
        if key in item:
            source[key] = item[key]
    return adapt_stage1_intent(source, question=intent.question)


def _execute_multi_query_stage3(
    *,
    question: str,
    intent: Stage3Intent,
    stage2_result: Any,
    writer: AnswerWriter,
    cache: Any | None = None,
) -> Stage3Result:
    raw_subresults = stage2_result.get("subresults", []) if isinstance(stage2_result, Mapping) else []
    by_id = {
        str(item.get("subquery_id")): item
        for item in raw_subresults
        if isinstance(item, Mapping) and item.get("subquery_id")
    }
    subresults: list[dict[str, Any]] = []
    facts: list[Stage3Fact] = []
    calculations: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    warnings = list(intent.warnings)
    for item in intent.query_plan:
        subquery_id = str(item.get("subquery_id") or f"subquery-{len(subresults) + 1}")
        sub_intent = _subquery_intent(intent, item)
        subresult = _execute_stage3(
            question=question,
            intent=sub_intent,
            stage2_result=by_id.get(subquery_id, {}),
            writer=writer,
            write_answer=False,
            cache=cache,
        )
        sub_dict = subresult.to_dict()
        sub_dict["subquery_id"] = subquery_id
        subresults.append(sub_dict)
        facts.extend(Stage3Fact.from_dict(value) for value in subresult.facts)
        calculations.extend({**value, "subquery_id": subquery_id} for value in subresult.calculations)
        comparisons.extend({**value, "subquery_id": subquery_id} for value in subresult.comparison_results)
        events.extend({**value, "subquery_id": subquery_id} for value in subresult.linked_events)
        warnings.extend(f"{subquery_id}: {value}" for value in subresult.warnings)

    bundle = adapt_stage2_bundle(stage2_result)
    citations = _build_citations(bundle.effective_documents(), facts, calculations, comparisons, events)
    success_count = sum(item.get("status") == "success" for item in subresults)
    result_status = (
        "success"
        if success_count == len(subresults) and subresults
        else "partial_success"
        if success_count
        else "insufficient_evidence"
    )
    result = Stage3Result(
        status=result_status,
        facts=[fact.to_dict() for fact in facts],
        calculations=calculations,
        comparison_results=comparisons,
        linked_events=events,
        citations=citations,
        warnings=warnings,
        subresults=subresults,
        trace=[
            "stage3_start",
            "question_type=multi_query",
            f"subqueries={len(subresults)}",
            f"successful_subqueries={success_count}",
            f"facts={len(facts)}",
        ],
    )
    valid, validation_warnings = validate_stage3_result(result, intent)
    if not valid:
        result = replace(
            result,
            status="insufficient_evidence",
            warnings=[*result.warnings, *validation_warnings],
            trace=[*result.trace, "stage3_validation_failed"],
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


def _intent_from_state(state: Mapping[str, Any]) -> Stage3Intent:
    raw_intent = state.get("intent", {})
    question = str(state.get("question", ""))
    state_route = state.get("route")
    if isinstance(raw_intent, Stage3Intent):
        if state_route is not None and str(state_route) != raw_intent.route:
            return adapt_stage1_intent({**raw_intent.to_dict(), "route": state_route}, question=question or raw_intent.question)
        return raw_intent
    source = dict(raw_intent) if isinstance(raw_intent, Mapping) else {}
    if state_route is not None:
        source["route"] = state_route
    question = question or str(source.get("raw_question", source.get("question", "")))
    return adapt_stage1_intent(source, question=question)


def _message(answer: str) -> Any:
    try:
        from langchain_core.messages import AIMessage
    except ImportError:
        return {"role": "assistant", "content": answer}
    return AIMessage(content=answer)


def build_stage3_node(
    *,
    answer_client: Any | None = None,
    answer_writer: AnswerWriter | None = None,
    cache: Any | None = None,
) -> Callable[[Mapping[str, Any]], Stage3NodeOutput]:
    """Build the single LangGraph-compatible Stage3 node.

    The returned callable reads a structural subset of the shared state and
    returns only a partial update.  It never performs retrieval or Stage4
    validation and never mutates the input mapping.
    """

    if answer_client is not None and answer_writer is not None:
        raise ValueError("answer_client와 answer_writer를 동시에 지정할 수 없습니다.")
    writer = answer_writer or AnswerWriter(answer_client)

    def stage3_node(state: Mapping[str, Any]) -> Stage3NodeOutput:
        intent = _intent_from_state(state)
        question = str(state.get("question", intent.question))
        try:
            result = _execute_stage3(
                question=question,
                intent=intent,
                stage2_result=state.get("stage2_result", {}),
                writer=writer,
                cache=cache,
            )
        except Exception as error:  # noqa: BLE001 - node boundary must reach Stage4
            result = Stage3Result(
                status="error",
                answer="Stage3 처리 중 근거를 구성할 수 없습니다.",
                warnings=[f"stage3_error: {type(error).__name__}"],
                trace=["stage3_failed"],
            )

        update: Stage3NodeOutput = {"stage3_result": result.to_dict()}
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

    return stage3_node


__all__ = ["build_stage3_node"]
