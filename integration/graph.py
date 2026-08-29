"""LangGraph composition for the shared Stage1~Stage4 execution State."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from integration.supervisor import build_planner_tool, build_supervisor_node, retry_search_tool
from shared_state import AgentState

StateNode = Callable[[Mapping[str, Any]], Mapping[str, Any]]


@dataclass(frozen=True)
class StageNodes:
    """Stage implementations and optional injected Supervisor/tool adapters."""

    stage1: StateNode
    stage2: StateNode
    stage3: StateNode
    stage4: StateNode
    supervisor: StateNode | None = None
    calculation_planner: StateNode | None = None


def _supervisor_route(state: Mapping[str, Any]) -> str:
    return str(state.get("supervisor_action", "fail_closed"))


def _blocked_route(route: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda _state: {"route": route}


def _set_phase(phase: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    return lambda _state: {"supervisor_phase": phase}


def _planner_return(state: Mapping[str, Any]) -> str:
    return "supervisor_after_stage3" if state.get("supervisor_phase") == "after_stage3" else "supervisor_after_stage1"


def build_graph(nodes: StageNodes):
    """Build the bounded Supervisor-controlled four-stage graph."""

    supervisor = nodes.supervisor or build_supervisor_node()
    planner = nodes.calculation_planner or build_planner_tool()
    builder = StateGraph(AgentState)
    builder.add_node("stage1", nodes.stage1)
    builder.add_node("phase_after_stage1", _set_phase("after_stage1"))
    builder.add_node("supervisor_after_stage1", supervisor)
    builder.add_node("calculation_planner", planner)
    builder.add_node("stage2", nodes.stage2)
    builder.add_node("phase_after_stage2", _set_phase("after_stage2"))
    builder.add_node("retry_search", retry_search_tool)
    builder.add_node("supervisor_after_stage2", supervisor)
    builder.add_node("stage3", nodes.stage3)
    builder.add_node("phase_after_stage3", _set_phase("after_stage3"))
    builder.add_node("supervisor_after_stage3", supervisor)
    builder.add_node("stage4", nodes.stage4)
    builder.add_node("phase_after_stage4", _set_phase("after_stage4"))
    builder.add_node("supervisor_after_stage4", supervisor)
    builder.add_node("clarify", _blocked_route("need_clarify"))
    builder.add_node("unanswerable", _blocked_route("unanswerable"))
    builder.add_node("fail_closed", _blocked_route("unsafe"))

    builder.add_edge(START, "stage1")
    builder.add_edge("stage1", "phase_after_stage1")
    builder.add_edge("phase_after_stage1", "supervisor_after_stage1")
    builder.add_conditional_edges("supervisor_after_stage1", _supervisor_route, {
        "run_stage2": "stage2",
        "run_calculation_planner": "calculation_planner",
        "request_clarification": "clarify",
        "unanswerable": "unanswerable",
        "fail_closed": "fail_closed",
    })
    builder.add_conditional_edges("calculation_planner", _planner_return, {
        "supervisor_after_stage1": "supervisor_after_stage1",
        "supervisor_after_stage3": "supervisor_after_stage3",
    })
    builder.add_edge("clarify", "stage4")
    builder.add_edge("unanswerable", "stage4")
    builder.add_edge("fail_closed", "stage4")

    builder.add_edge("stage2", "phase_after_stage2")
    builder.add_edge("phase_after_stage2", "supervisor_after_stage2")
    builder.add_conditional_edges("supervisor_after_stage2", _supervisor_route, {
        "run_stage3": "stage3",
        "retry_search": "retry_search",
        "unanswerable": "unanswerable",
        "fail_closed": "fail_closed",
    })
    builder.add_edge("retry_search", "stage2")

    builder.add_edge("stage3", "phase_after_stage3")
    builder.add_edge("phase_after_stage3", "supervisor_after_stage3")
    builder.add_conditional_edges("supervisor_after_stage3", _supervisor_route, {
        "run_stage4": "stage4",
        "run_calculation_planner": "calculation_planner",
        "fail_closed": "fail_closed",
    })
    builder.add_edge("stage4", "phase_after_stage4")
    builder.add_edge("phase_after_stage4", "supervisor_after_stage4")
    builder.add_edge("supervisor_after_stage4", END)
    return builder.compile()


__all__ = ["StageNodes", "StateNode", "build_graph"]
