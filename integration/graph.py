"""LangGraph composition for the shared Stage1~Stage4 execution State."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from shared_state import AgentState


StateNode = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class StageNodes:
    """Stage implementations injected by the team integration layer."""

    stage1: StateNode
    stage2: StateNode
    stage3: StateNode
    stage4: StateNode


def _after_stage1(state: Mapping[str, Any]) -> str:
    """Skip retrieval/analysis for blocked routes and continue to Stage4."""

    return "stage2" if state.get("route") == "ok" else "stage4"


def build_graph(nodes: StageNodes):
    """Build the minimal four-node graph around the shared AgentState.

    Stage implementations are deliberately injected so this integration layer
    owns only graph composition and does not duplicate Stage business logic.
    """

    builder = StateGraph(AgentState)
    builder.add_node("stage1", nodes.stage1)
    builder.add_node("stage2", nodes.stage2)
    builder.add_node("stage3", nodes.stage3)
    builder.add_node("stage4", nodes.stage4)
    builder.add_edge(START, "stage1")
    builder.add_conditional_edges(
        "stage1",
        _after_stage1,
        {"stage2": "stage2", "stage4": "stage4"},
    )
    builder.add_edge("stage2", "stage3")
    builder.add_edge("stage3", "stage4")
    builder.add_edge("stage4", END)
    return builder.compile()


__all__ = ["StageNodes", "StateNode", "build_graph"]
