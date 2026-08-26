from dotenv import load_dotenv

from state import AgentState
from stage1 import CorpusIndex, build_intent

load_dotenv()

# 프로세스 시작 시 1회만 로드 (README 권장 사용법)
corpus_index = CorpusIndex.load()


def query_interpreter(state: AgentState):
    """Stage1: 자연어 질문 -> Intent JSON + route.

    그래프의 첫 노드다. 최초 입력은 messages만 채워져 있으므로(question은 아직 없음)
    question이 없으면 마지막 메시지에서 뽑아 이후 노드(chatbot 등)가 쓸 수 있게 함께 반환한다.
    """
    print("----- [STAGE1] QUERY INTERPRETER -----")
    question = state.get("question")
    if not question:
        last_message = state["messages"][-1]
        question = getattr(last_message, "content", last_message)

    intent_obj = build_intent(question, corpus_index)
    intent_json = intent_obj.to_dict()
    route = intent_json.get("route", "unanswerable")

    return {
        "question": question,
        "intent": intent_json,
        "route": route,
    }
