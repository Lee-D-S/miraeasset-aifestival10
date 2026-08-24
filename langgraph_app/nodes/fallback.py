from langgraph_app.state import GraphState


def fallback_node(state: GraphState) -> dict:
    return {
        "answer": "제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.",
        "status": "fallback",
        "trace": [state.get("error") or "fallback"],
    }

