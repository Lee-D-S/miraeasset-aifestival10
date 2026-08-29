from common.fallback import format_fallback_answer
from langgraph_rag.contracts import AlternativeFinderPort
from langgraph_rag.state import GraphState


def make_fallback_node(finder: AlternativeFinderPort | None = None):
    def fallback_node(state: GraphState) -> dict:
        alternatives = {}
        if finder is not None:
            try:
                alternatives = finder.find(state.get("question", ""))
            except Exception as error:
                return {
                    "answer": format_fallback_answer(
                        state.get("fallback_reason") or state.get("error") or "대체 참고 자료 검색 중 오류가 발생했습니다."
                    ),
                    "alternative_documents": {},
                    "status": "fallback",
                    "trace": [str(error), "alternative_search_error"],
                }
        answer = format_fallback_answer(
            state.get("fallback_reason")
            or state.get("evaluation_reason")
            or state.get("error")
            or "검색 결과가 질문의 조건과 일치하지 않았습니다.",
            same_company=alternatives.get("same_company", []),
            same_period=alternatives.get("same_period", []),
        )
        return {
            "answer": answer,
            "alternative_documents": alternatives,
            "status": "fallback",
            "trace": [state.get("error") or "fallback"],
        }

    return fallback_node


def fallback_node(state: GraphState) -> dict:
    return {
        "answer": format_fallback_answer(
            state.get("error") or "검색 결과가 질문의 조건과 일치하지 않았습니다."
        ),
        "status": "fallback",
        "trace": [state.get("error") or "fallback"],
    }
