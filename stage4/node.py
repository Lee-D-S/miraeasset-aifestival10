from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Callable

from stage4.citation import validate_citations
from stage4.contracts import Stage4Result
from stage4.numeric import validate_numeric_answer
from stage4.semantic import validate_semantics


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
        trace = ["stage4_start"]
        regenerated = False

        try:
            stage2_result = state.get("stage2_result") if isinstance(state.get("stage2_result"), Mapping) else None
            numeric, citation = validate_numeric_answer(answer, stage3_result), validate_citations(answer, stage3_result, stage2_result)
            if numeric["pass"] and citation["pass"]:
                semantic = validate_semantics(client, question=question, intent=intent, stage3_result=stage3_result, answer=answer)
            else:
                semantic = {"pass": False, "issues": [*numeric.get("errors", []), *citation.get("errors", [])], "unsupported_claims": [], "missing_aspects": [], "summary": "결정론적 검증 실패"}
            valid = bool(numeric.get("pass") and citation.get("pass") and semantic.get("pass"))
            if not valid:
                if client is None:
                    raise RuntimeError("Stage4 regeneration client is not configured")
                regenerated = True
                repair = {"numeric_check": numeric, "citation_check": citation, "semantic_check": semantic}
                regenerated_answer = client.generate_text([{
                    "role": "user",
                    "content": "공시 근거만 사용해 답변을 수정하세요. 근거에 없는 수치·사실은 삭제하고, "
                    "계산 결과를 변경하지 마세요. 출처 표기를 포함하세요. 한 번의 최종 답변만 출력하세요.\n"
                    + json.dumps({"question": question, "intent": intent, "stage3_result": dict(stage3_result), "answer": answer, "validation": repair}, ensure_ascii=False),
                }])
                answer = str(regenerated_answer)
                numeric, citation = validate_numeric_answer(answer, stage3_result), validate_citations(answer, stage3_result, stage2_result)
                semantic = validate_semantics(client, question=question, intent=intent, stage3_result=stage3_result, answer=answer)
                valid = bool(numeric.get("pass") and citation.get("pass") and semantic.get("pass"))
            if not valid:
                answer = _FAILURE_ANSWER
                status = "validation_failed"
                trace.append("validation_failed")
            else:
                status = "regenerated" if regenerated else "success"
                trace.append("validated")
        except Exception as error:  # workflow boundary must fail closed
            answer = _FAILURE_ANSWER
            status = "validation_failed"
            warnings.append(f"stage4_error: {type(error).__name__}")
            trace.append("validation_error")

        result = Stage4Result(status=status, answer=answer, numeric_check=numeric, citation_check=citation, semantic_check=semantic, regenerated=regenerated, warnings=warnings, trace=trace)
        return {"stage4_result": result.to_dict(), "answer": answer, "messages": [_message(answer)]}

    return stage4_node


__all__ = ["build_stage4_node"]
