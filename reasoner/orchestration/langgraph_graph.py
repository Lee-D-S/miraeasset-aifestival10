from __future__ import annotations

from typing import Any

from reasoner.agents.answer import AnswerWriter
from reasoner.node import build_reasoner_node
from reasoner.state import ReasonerNodeGraphState


def build_graph(
    *,
    answer_writer: AnswerWriter | None = None,
    answer_client: Any | None = None,
):
    """Build the optional one-node compatibility graph.

    The team's outer graph owns the four Stage nodes.  This graph exists only
    for callers that still request ``ReasonerService(execution_mode='langgraph')``
    and therefore contains exactly one Reasoner node.
    """

    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError as error:  # pragma: no cover - optional dependency
        raise RuntimeError("LangGraph 실행에는 langgraph 패키지가 필요합니다.") from error

    builder = StateGraph(ReasonerNodeGraphState)
    builder.add_node(
        "reasoner",
        build_reasoner_node(answer_writer=answer_writer, answer_client=answer_client),
    )
    builder.add_edge(START, "reasoner")
    builder.add_edge("reasoner", END)
    return builder.compile()


__all__ = ["build_graph"]
