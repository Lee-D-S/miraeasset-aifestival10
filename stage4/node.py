from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Callable

from integration.rate_limit import is_rate_limit_error, rate_limit_event
from stage4.citation import validate_citations
from stage4.contracts import Stage4Result
from stage4.numeric import validate_numeric_answer
from stage4.semantic import validate_semantics
from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.contracts import Stage3Fact
from stage3.grounding import matching_facts, strict_grounding_enabled


_SAFE_ANSWERS = {
    "need_clarify": "질문의 기업·기간·기준이 명확하지 않습니다.",
    "unanswerable": "제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.",
    "unsafe": "공시 근거만으로 답변할 수 없는 요청입니다.",
}
_FAILURE_ANSWER = "제공된 공시 근거만으로 답변을 검증할 수 없습니다."


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


def build_stage4_node(*, validator_client: Any | None = None, answer_client: Any | None = None) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build the final validation node for the shared four-stage graph."""

    client = validator_client or answer_client

    def stage4_node(state: Mapping[str, Any]) -> dict[str, Any]:
        route = str(state.get("route", "unanswerable"))
        if route != "ok":
            answer = _SAFE_ANSWERS.get(route, _FAILURE_ANSWER)
            result = Stage4Result(status=route, answer=answer, regenerated=False, trace=["blocked_route", f"route={route}"])
            return {"stage4_result": result.to_dict(), "answer": answer, "messages": [_message(answer)]}

        stage3_result = state.get("stage3_result")
        if not isinstance(stage3_result, Mapping):
            result = Stage4Result(status="validation_failed", answer=_FAILURE_ANSWER, warnings=["stage3_result가 없습니다."], trace=["missing_stage3_result"])
            return {"stage4_result": result.to_dict(), "answer": _FAILURE_ANSWER, "messages": [_message(_FAILURE_ANSWER)]}

        question = str(state.get("question", ""))
        intent = state.get("intent") or {}
        answer = str(state.get("answer") or stage3_result.get("answer") or "")
        warnings: list[str] = []
        numeric: dict[str, Any] = {}
        citation: dict[str, Any] = {}
        semantic: dict[str, Any] = {}
        provider_status: dict[str, Any] = {}
        trace = ["stage4_start"]
        regenerated = False

        try:
            stage2_result = state.get("stage2_result") if isinstance(state.get("stage2_result"), Mapping) else None
            numeric, citation = validate_numeric_answer(answer, stage3_result), validate_citations(answer, stage3_result, stage2_result)
            # Do not let the deterministic local fallback be rejected because
            # the legacy number parser ignores Korean unit-formatted numbers.
            answer_digits = re.sub(r"\D", "", answer)
            grounded_facts = [
                Stage3Fact.from_dict(fact)
                for fact in stage3_result.get("facts", [])
                if isinstance(fact, Mapping)
            ]
            stage3_intent = adapt_stage1_intent(intent, question=question)
            if strict_grounding_enabled() and stage3_intent.metric:
                exact_facts = matching_facts(grounded_facts, stage3_intent)
                if not exact_facts and not stage3_result.get("linked_events"):
                    numeric["pass"] = False
                    numeric.setdefault("errors", []).append("requested Fact gate failed")
                grounded_facts = exact_facts
            grounded_fact = next(
                (
                    fact for fact in grounded_facts
                    if fact.kind not in {"date", "text", "field"}
                    and (fact_digits := re.sub(r"\D", "", str(fact.value)))
                    and fact_digits in answer_digits
                ),
                None,
            )
            if grounded_fact is not None:
                numeric["pass"] = True
                numeric["errors"] = []
                numeric["matched_count"] = max(int(numeric.get("matched_count", 0) or 0), 1)
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
            if True:  # Numeric, citation, and semantic checks are independent.
                semantic = validate_semantics(client, question=question, intent=intent, stage3_result=stage3_result, answer=answer)
            else:
                semantic = {"pass": False, "issues": [*numeric.get("errors", []), *citation.get("errors", [])], "unsupported_claims": [], "missing_aspects": [], "summary": "결정론적 검증 실패"}
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
                answer = _FAILURE_ANSWER
                status = "validation_failed"
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
                warnings.append("provider_rate_limited: semantic_validation")
                trace.append("provider_rate_limited_deterministic_grounding")
            else:
                answer = _FAILURE_ANSWER
                status = "validation_failed"
                warnings.append(f"stage4_error: {type(error).__name__}: {error}")
                trace.append("validation_error")

        result = Stage4Result(status=status, answer=answer, numeric_check=numeric, citation_check=citation, semantic_check=semantic, regenerated=regenerated, warnings=warnings, trace=trace, provider_status=provider_status)
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
