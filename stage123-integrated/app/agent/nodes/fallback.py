from langchain_core.messages import AIMessage

from state import AgentState

# Stage1 route별 기본 안내 문구. intent.clarify_message가 있으면 그쪽을 우선 사용한다.
_DEFAULT_MESSAGES = {
    "need_clarify": "질문을 조금 더 구체적으로 알려주시겠어요?",
    "unanswerable": "죄송합니다. 제공된 공시 데이터 범위에서는 답변할 수 없습니다.",
    "unsafe": "죄송합니다. 해당 요청에는 답변드릴 수 없습니다.",
}


def _respond_with_intent_message(state: AgentState, route_key: str):
    intent = state.get("intent") or {}
    answer = intent.get("clarify_message") or _DEFAULT_MESSAGES[route_key]
    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],
    }


def clarify_node(state: AgentState):
    """TODO(stage3): 역질문 로직 미구현. 현재는 Stage1의 clarify_message를 그대로 반환하는 임시 노드."""
    print("----- [CLARIFY] (stage3 미구현: 임시 응답) -----")
    return _respond_with_intent_message(state, "need_clarify")


def unanswerable_node(state: AgentState):
    """TODO(stage3): 한계 고지 로직 미구현. 현재는 Stage1의 reject 메시지를 그대로 반환하는 임시 노드."""
    print("----- [UNANSWERABLE] (stage3 미구현: 임시 응답) -----")
    return _respond_with_intent_message(state, "unanswerable")


def unsafe_node(state: AgentState):
    """TODO(stage3): 거절 로직 미구현. 현재는 기본 거절 메시지를 반환하는 임시 노드."""
    print("----- [UNSAFE] (stage3 미구현: 임시 응답) -----")
    return _respond_with_intent_message(state, "unsafe")
