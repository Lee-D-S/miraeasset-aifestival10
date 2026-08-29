"""Execution service for the four-stage graph."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AnyMessage

from integration.graph import StageNodes, build_graph
from shared_state import AgentState, make_initial_agent_state


class StagePipeline:
    """Invoke a compiled four-stage graph from an API or test boundary."""

    def __init__(self, nodes: StageNodes, *, recursion_limit: int = 24):
        self.graph = build_graph(nodes)
        self.recursion_limit = recursion_limit

    def invoke(
        self,
        *,
        question_id: str,
        question: str,
        messages: Sequence[AnyMessage] | None = None,
    ) -> AgentState:
        initial_state = make_initial_agent_state(
            question_id=question_id,
            question=question,
            messages=messages,
        )
        return self.graph.invoke(initial_state, config={"recursion_limit": self.recursion_limit})


__all__ = ["StagePipeline"]
