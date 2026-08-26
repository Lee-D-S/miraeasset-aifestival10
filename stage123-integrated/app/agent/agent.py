import os
import sys

# 현재 파일(agent.py) 기준으로 상위 상위 폴더(miraeasset-firstpenguin)를 모듈 검색 경로에 등록
PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../..")
)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv
load_dotenv()

from langgraph.prebuilt import tools_condition
from langgraph.graph import StateGraph, MessagesState, START, END

from state import AgentState
from nodes import (
    query_interpreter,
    chatbot,
    tool_node,
    context_organizer,
    response_generator,
    query_transformer,
    clarify_node,
    unanswerable_node,
    unsafe_node,
)
from edges import decide_to_generate, check_hallucinations, route_decision


# 그래프 생성 본문

graph_builder = StateGraph(AgentState, input_schema=MessagesState)
graph_builder.add_node("stage1_understand", query_interpreter)
graph_builder.add_node("chatbot", chatbot)
graph_builder.add_node("tools", tool_node)
graph_builder.add_node("clarify_node", clarify_node)
graph_builder.add_node("unanswerable_node", unanswerable_node)
graph_builder.add_node("unsafe_node", unsafe_node)

graph_builder.add_edge(START, "stage1_understand")
graph_builder.add_conditional_edges(
    "stage1_understand",
    route_decision,
    {
        "chatbot_node": "chatbot",
        "clarify_node": "clarify_node",
        "unanswerable_node": "unanswerable_node",
        "unsafe_node": "unsafe_node",
    },
)
# stage3(응답 생성) 미구현 구간 - 팀원이 만들면 이 세 노드를 실제 로직으로 교체
graph_builder.add_edge("clarify_node", END)
graph_builder.add_edge("unanswerable_node", END)
graph_builder.add_edge("unsafe_node", END)

graph_builder.add_conditional_edges(
    "chatbot",
    tools_condition,
    {
        "tools": "tools",
        END: END,
    }
)

graph_builder.add_node("context_organizer", context_organizer)
graph_builder.add_node("query_transformer", query_transformer)
graph_builder.add_node("response_generator", response_generator)

graph_builder.add_edge("tools", "context_organizer")
graph_builder.add_conditional_edges(
    "context_organizer",
    decide_to_generate,
    {
        "query_transformer": "query_transformer",
        "response_generator": "response_generator",
    },
)
graph_builder.add_edge("query_transformer", "tools")

graph_builder.add_conditional_edges(
    "response_generator",
    check_hallucinations,
    {
        "not supported": "response_generator",
        "support": END
    },
)

graph = graph_builder.compile()

async def astream():
    response = graph.astream(
        {
            "messages": [
                #"가장 최근 공시 5개?"
                "삼성SDI를 제외한 2차 전지 기업 중에 가장 매출이 큰 기업과 삼성전자의 매출을 비교해줘."
            ]
        }
    )
    async for chunk in response:
        for node, state in chunk.items():
            if node:
                print("---", node, "---")
                print(state)
                print("="*60)
            if (
                isinstance(state, dict)
                and "messages" in state
                and state["messages"]
            ):
                last_msg = state["messages"][-1]  # 가장 최근 추가된 메시지 출력
                # BaseMessage 객체면 .content 출력, 일반 str이면 그대로 출력
                content = getattr(last_msg, "content", last_msg)
                print(f"[{node} 메시지]: {content}")

        print("="*60)

if __name__ == "__main__":
    try:
        png_bytes = graph.get_graph().draw_mermaid_png()
        with open("graph.png", "wb") as f:
            f.write(png_bytes)
    except Exception:
        pass
    import asyncio
    asyncio.run(astream())


