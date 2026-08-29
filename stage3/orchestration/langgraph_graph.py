from __future__ import annotations

from typing import Any

from stage3.agents.answer import AnswerWriter
from stage3.node import build_stage3_node
from stage3.state import Stage3NodeGraphState


def build_graph(
    *,
    answer_writer: AnswerWriter | None = None,
    answer_client: Any | None = None,
):
    """Build the optional one-node compatibility graph.

    The team's outer graph owns the four Stage nodes.  This graph exists only
    for callers that still request ``Stage3Service(execution_mode='langgraph')``
    and therefore contains exactly one Stage3 node.
    """

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError("LangGraph 실행에는 langgraph 패키지가 필요합니다.") from error

    builder = StateGraph(Stage3NodeGraphState)
    builder.add_node(
        "stage3",
        build_stage3_node(answer_writer=answer_writer, answer_client=answer_client),
    )
    builder.add_edge(START, "stage3")
    builder.add_edge("stage3", END)
    return builder.compile()


__all__ = ["build_graph"]
