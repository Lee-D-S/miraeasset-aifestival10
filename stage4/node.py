from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Callable

from integration.rate_limit import is_rate_limit_error, rate_limit_event
from integration.failure_response import build_failure_response
from stage4.citation import validate_citations
from stage4.contracts import Stage4Result
from stage4.numeric import validate_numeric_answer
from stage4.semantic import deterministic_semantic_fallback, validate_semantics
from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.contracts import Stage3Fact
from stage3.deterministic.calculation_planner import validate_analysis_plan
from stage3.grounding import matching_facts, requested_aggregation_scope, strict_grounding_enabled


def _missing_segment_facts(answer: str, facts: list[Stage3Fact]) -> list[str]:
    """Require every extracted segment amount to appear in the final answer."""

    answer_digits = re.sub(r"\D", "", answer)
    missing: list[str] = []
    for fact in facts:
        if fact.kind != "numeric" or fact.unit == "%":
            continue
        candidates = {str(fact.raw_value or ""), str(fact.value or ""), str(fact.normalized_value or "")}
        candidates = {re.sub(r"\D", "", value) for value in candidates if re.sub(r"\D", "", value)}
        if not any(value in answer_digits for value in candidates):
            missing.append(str(fact.table_context.get("row_label") or fact.label or fact.document_id))
    return missing


def _message(answer: str) -> Any:
    try:
        from langchain_core.messages import AIMessage
    except ImportError:
        return {"role": "assistant", "content": answer}
    return AIMessage(content=answer)


def _allow_grounded_semantic_uncertainty(
    *,
    answer: str,
    numeric: Mapping[str, Any],
    citation: Mapping[str, Any],
    semantic: Mapping[str, Any],
) -> bool:
    """Allow only explicitly grounded lookup answers past an uncertain verdict."""
    if not numeric.get("pass") or not citation.get("pass"):
        return False
    if not answer.strip():
        return False
    if semantic.get("unsupported_claims") or semantic.get("missing_aspects"):
        return False
    # Numeric validation is authoritative for numeric claims, while citation
    # validation confirms the Stage3 evidence chain. Together they provide a
    # bounded grounding fallback when the semantic provider is merely unsure.
    return True


def _provider_failure_event(error: BaseException, *, client: Any | None) -> dict[str, Any]:
    """Return a non-secret provider failure event for the public trace."""

    event: dict[str, Any] = {
        "status": "provider_error",
        "operation": "semantic_validation",
        "source": "provider_boundary",
        "error_type": type(error).__name__,
    }
    provider_status = getattr(client, "last_provider_status", {})
    if isinstance(provider_status, Mapping):
        for key in ("status_code", "provider_code", "source"):
            if key in provider_status:
                event[key] = provider_status[key]
    limiter = getattr(client, "rate_limiter", None)
    snapshot = getattr(limiter, "snapshot", None)
    if callable(snapshot):
        event["limiter"] = snapshot()
    return event


def _exact_multi_query_facts(
    *,
    stage3_result: Mapping[str, Any],
    raw_intent: Mapping[str, Any],
    question: str,
) -> tuple[list[Stage3Fact], list[str]]:
    """Apply the Fact gate per subquery while allowing explicit partial results."""

    top_facts = [
        Stage3Fact.from_dict(fact)
        for fact in stage3_result.get("facts", [])
        if isinstance(fact, Mapping)
    ]
    subresults = stage3_result.get("subresults", [])
    by_id = {
        str(item.get("subquery_id")): item
        for item in subresults
        if isinstance(item, Mapping) and item.get("subquery_id")
    }
    matched: list[Stage3Fact] = []
    missing: list[str] = []
    for item in raw_intent.get("query_plan", []):
        if not isinstance(item, Mapping):
            continue
        subquery_id = str(item.get("subquery_id") or "subquery")
        source = dict(raw_intent)
        source["query_plan"] = []
        for key in ("metric", "question_type", "calculation", "basis", "time", "manifest_filter"):
            if key in item:
                source[key] = item[key]
        sub_intent = adapt_stage1_intent(source, question=question)
        subresult = by_id.get(subquery_id, {})
        candidates = [
            Stage3Fact.from_dict(fact)
            for fact in subresult.get("facts", top_facts)
            if isinstance(fact, Mapping)
        ]
        exact = matching_facts(candidates or top_facts, sub_intent)
        matched.extend(exact)
        if str(subresult.get("status", "")) != "success":
            missing.append(subquery_id)
    return matched, missing


def _exact_plan_facts(
    *,
    stage3_result: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> tuple[list[Stage3Fact], list[str]]:
    """Apply the same requirement gate used by canonical plan execution."""

    facts = [
        Stage3Fact.from_dict(fact)
        for fact in stage3_result.get("facts", [])
        if isinstance(fact, Mapping)
    ]
    matched: list[Stage3Fact] = []
    missing: list[str] = []
    for requirement in plan.get("requirements", []):
        if not isinstance(requirement, Mapping):
            continue
        identifier = str(requirement.get("id", "requirement"))
        metric = str(requirement.get("metric", ""))
        companies = {str(item) for item in requirement.get("companies", []) if str(item).strip()}
        periods = {str(item) for item in requirement.get("periods", []) if str(item).strip()}
        candidates = [
            fact for fact in facts
            if fact.kind == "numeric"
            and fact.metric == metric
            and (not companies or str(fact.company) in companies)
            and (not periods or any(str(period) in str(fact.period or "") for period in periods))
        ]
        if requirement.get("required", True) and not candidates:
            missing.append(identifier)
        matched.extend(candidates)
    matched.extend(fact for fact in facts if fact.kind == "derived")
    return matched, missing


def build_stage4_node(*, validator_client: Any | None = None, answer_client: Any | None = None) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the final validation node for the shared four-stage graph."""

    client = validator_client or answer_client

    def stage4_node(state: Mapping[str, Any]) -> dict[str, Any]:
        route = str(state.get("route", "unanswerable"))
        if route != "ok":
            failure = build_failure_response(state)
            answer = failure.answer
            result = Stage4Result(
                status=route,
                answer=answer,
                regenerated=False,
                trace=["blocked_route", f"route={route}"],
                failure_reason_code=failure.reason_code.value,
            )
            return {"stage4_result": result.to_dict(), "answer": answer, "messages": [_message(answer)]}

        stage3_result = state.get("stage3_result")
        if not isinstance(stage3_result, Mapping):
            failure = build_failure_response(
                state,
                reason_hint="최종 답변을 검증할 Stage3 분석 결과가 없습니다.",
            )
            result = Stage4Result(
                status="validation_failed",
                answer=failure.answer,
                warnings=["stage3_result가 없습니다."],
                trace=["missing_stage3_result"],
                failure_reason_code=failure.reason_code.value,
            )
            return {"stage4_result": result.to_dict(), "answer": failure.answer, "messages": [_message(failure.answer)]}

        question = str(state.get("question", ""))
        intent = state.get("intent") or {}
        answer = str(state.get("answer") or stage3_result.get("answer") or "")
        warnings: list[str] = []
        numeric: dict[str, Any] = {}
        citation: dict[str, Any] = {}
        semantic: dict[str, Any] = {}
        provider_status: dict[str, Any] = {}
        trace = ["stage4_start"]
        regenerated = int(state.get("regeneration_attempts", 0) or 0) > 0
        failure_reason_code: str | None = None

        try:
            stage2_result = state.get("stage2_result") if isinstance(state.get("stage2_result"), Mapping) else None
            intent_source = dict(intent) if isinstance(intent, Mapping) else {}
            if isinstance(state.get("analysis_plan"), Mapping) and state["analysis_plan"].get("status") == "ready":
                intent_source["analysis_plan"] = dict(state["analysis_plan"])
            stage3_intent = adapt_stage1_intent(intent_source, question=question)
            # Only re-check facts against the intent for a single-metric
            # lookup/calculation; a multi-query or list/compare intent isn't
            # what fact_matches_intent's company/period/basis/scope gate is
            # shaped for, so leave those to the existing "requested Fact
            # gate" below instead of double-filtering here.
            numeric_intent = stage3_intent if strict_grounding_enabled() and stage3_intent.metric else None
            numeric = validate_numeric_answer(answer, stage3_result, numeric_intent)
            citation = validate_citations(answer, stage3_result, stage2_result)
            # Do not let the deterministic local fallback be rejected because
            # the legacy number parser ignores Korean unit-formatted numbers.
            answer_digits = re.sub(r"\D", "", answer)
            grounded_facts = [
                Stage3Fact.from_dict(fact)
                for fact in stage3_result.get("facts", [])
                if isinstance(fact, Mapping)
            ]
            missing_subqueries: list[str] = []
            if strict_grounding_enabled() and (
                stage3_intent.metric or stage3_intent.analysis_plan.get("status") == "ready"
            ):
                if stage3_intent.analysis_plan.get("status") == "ready":
                    try:
                        plan = validate_analysis_plan(stage3_intent.analysis_plan)
                    except (TypeError, ValueError) as error:
                        numeric["pass"] = False
                        numeric.setdefault("errors", []).append(
                            f"invalid analysis_plan: {type(error).__name__}"
                        )
                        exact_facts = []
                    else:
                        exact_facts, missing_subqueries = _exact_plan_facts(
                            stage3_result=stage3_result,
                            plan=plan,
                        )
                elif len(stage3_intent.query_plan) > 1:
                    exact_facts, missing_subqueries = _exact_multi_query_facts(
                        stage3_result=stage3_result,
                        raw_intent=intent if isinstance(intent, Mapping) else {},
                        question=question,
                    )
                    if missing_subqueries:
                        numeric.setdefault("errors", []).append(
                            "missing subquery evidence: " + ", ".join(missing_subqueries)
                        )
                else:
                    exact_facts = matching_facts(grounded_facts, stage3_intent)
                if not exact_facts and not stage3_result.get("linked_events"):
                    numeric["pass"] = False
                    numeric.setdefault("errors", []).append("requested Fact gate failed")
                grounded_facts = exact_facts
                if requested_aggregation_scope(stage3_intent) == "segment":
                    missing_segments = _missing_segment_facts(answer, exact_facts)
                    if missing_segments:
                        numeric["pass"] = False
                        numeric.setdefault("errors", []).append(
                            "missing segment facts in answer: " + ", ".join(missing_segments)
                        )
            segment_missing = any(
                str(error).startswith("missing segment facts in answer:")
                for error in numeric.get("errors", [])
            )
            grounded_fact = next(
                (
                    fact for fact in grounded_facts
                    if fact.kind not in {"date", "text", "field"}
                    and any(
                        fact_digits
                        and fact_digits in answer_digits
                        for fact_value in (
                            getattr(fact, "raw_value", ""),
                            fact.value,
                            fact.normalized_value,
                        )
                        for fact_digits in {
                            re.sub(
                                r"\D",
                                "",
                                str(
                                    int(fact_value)
                                    if isinstance(fact_value, float)
                                    and fact_value.is_integer()
                                    else fact_value
                                ),
                            )
                        }
                    )
                ),
                None,
            )
            if grounded_fact is not None and not segment_missing:
                numeric["pass"] = True
                numeric["errors"] = []
                numeric["matched_count"] = max(int(numeric.get("matched_count", 0) or 0), 1)
                if missing_subqueries:
                    numeric["errors"] = [
                        "missing subquery evidence: " + ", ".join(missing_subqueries)
                    ]
            if (
                not numeric.get("numbers")
                and re.search(r"\d", answer)
                and any(
                    isinstance(fact, Mapping)
                    and str(fact.get("kind", "numeric")).lower() not in {"date", "text", "field"}
                    for fact in stage3_result.get("facts", [])
                )
            ):
                numeric["numbers"] = [{"raw": "grounded", "normalized": "grounded", "unit": ""}]
                numeric["matched_count"] = 1
            if not answer.strip():
                numeric["pass"] = False
                numeric.setdefault("errors", []).append("answer is empty")
            if not citation.get("answer_has_source_marker"):
                citation["pass"] = False
                citation.setdefault("errors", []).append("answer source marker is missing")
            if client is None:
                semantic = deterministic_semantic_fallback(
                    answer=answer,
                    numeric_check=numeric,
                    citation_check=citation,
                )
                warnings.append(
                    "semantic_validation_provider_unavailable: deterministic_fallback"
                )
                trace.append("semantic_deterministic_fallback")
            else:
                try:
                    semantic = validate_semantics(
                        client,
                        question=question,
                        intent=intent,
                        stage3_result=stage3_result,
                        answer=answer,
                    )
                except Exception as error:
                    if numeric.get("pass") and citation.get("pass") and answer.strip():
                        provider_status = (
                            rate_limit_event(
                                error,
                                operation="semantic_validation",
                                client=client,
                            )
                            if is_rate_limit_error(error)
                            else _provider_failure_event(error, client=client)
                        )
                        semantic = deterministic_semantic_fallback(
                            answer=answer,
                            numeric_check=numeric,
                            citation_check=citation,
                        )
                        warnings.append(
                            "semantic_provider_fallback: " + type(error).__name__
                        )
                        trace.append(
                            "provider_rate_limited_deterministic_grounding"
                            if is_rate_limit_error(error)
                            else "semantic_provider_deterministic_fallback"
                        )
                    else:
                        raise
            if (
                not semantic.get("pass")
                and numeric.get("pass")
                and citation.get("pass")
                and answer.strip()
                and not semantic.get("unsupported_claims")
                and not semantic.get("missing_aspects")
            ):
                semantic = dict(semantic)
                semantic["pass"] = True
                semantic["summary"] = "Deterministic grounding and citation checks passed; semantic provider was uncertain."
                trace.append("semantic_uncertainty_grounded")
            valid = bool(numeric.get("pass") and citation.get("pass") and semantic.get("pass"))
            if not valid:
                failure = build_failure_response(
                    state,
                    numeric_check=numeric,
                    citation_check=citation,
                    semantic_check=semantic,
                )
                answer = failure.answer
                status = "validation_failed"
                failure_reason_code = failure.reason_code.value
                trace.append("validation_failed")
                if not semantic.get("pass", False):
                    trace.append("semantic_validation_failed")
                    warnings.append(
                        "semantic_failure: "
                        + str(semantic.get("summary") or "semantic validator rejected the answer")
                    )
                    for key in ("issues", "unsupported_claims", "missing_aspects"):
                        values = semantic.get(key)
                        if isinstance(values, list) and values:
                            warnings.append(f"semantic_{key}: {values}")
            else:
                status = "regenerated" if regenerated else "success"
                failure_reason_code = None
                trace.append("validated")
        except Exception as error:  # workflow boundary must fail closed
            if is_rate_limit_error(error) and numeric.get("pass") and citation.get("pass") and answer.strip():
                provider_status = rate_limit_event(
                    error,
                    operation="semantic_validation",
                    client=client,
                )
                semantic = {
                    "pass": True,
                    "issues": [],
                    "unsupported_claims": [],
                    "missing_aspects": [],
                    "summary": "Deterministic numeric and citation checks passed; semantic provider was rate-limited.",
                }
                status = "success"
                failure_reason_code = None
                warnings.append("provider_rate_limited: semantic_validation")
                trace.append("provider_rate_limited_deterministic_grounding")
            else:
                failure = build_failure_response(
                    state,
                    numeric_check=numeric,
                    citation_check=citation,
                    semantic_check=semantic,
                )
                answer = failure.answer
                status = "validation_failed"
                failure_reason_code = failure.reason_code.value
                warnings.append(f"stage4_error: {type(error).__name__}")
                trace.append("validation_error")

        result = Stage4Result(status=status, answer=answer, numeric_check=numeric, citation_check=citation, semantic_check=semantic, regenerated=regenerated, warnings=warnings, trace=trace, provider_status=provider_status, failure_reason_code=failure_reason_code)
        return {
            "stage4_result": result.to_dict(),
            "answer": answer,
            "messages": [_message(answer)],
            "validation_attempts": int(state.get("validation_attempts", 0) or 0) + 1,
        }

    return stage4_node


def build_answer_regeneration_node(*, answer_client: Any) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Regenerate the draft; a later Stage4 node remains the sole validator."""

    def regenerate(state: Mapping[str, Any]) -> dict[str, Any]:
        response = answer_client.generate_text([{
            "role": "user",
            "content": "공시 근거와 계산 결과만 사용해 답변을 다시 작성하고 출처 표기를 포함하세요.\n"
            + str({"question": state.get("question", ""), "stage3_result": state.get("stage3_result", {})}),
        }])
        return {
            "answer": str(response),
            "regeneration_attempts": int(state.get("regeneration_attempts", 0) or 0) + 1,
        }

    return regenerate


__all__ = ["build_answer_regeneration_node", "build_stage4_node"]
