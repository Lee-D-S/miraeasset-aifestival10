"""LangGraph composition for the shared Stage1~Stage4 execution State."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from langgraph.graph import END, START, StateGraph

from integration.supervisor import build_planner_tool, build_supervisor_node, retry_search_tool
from shared_state import AgentState, validate_node_update

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
    answer_regeneration: StateNode | None = None


# One route table per Supervisor phase: action -> next node (or END). A
# missing action for a phase fails closed, mirroring the Supervisor's own
# fail-closed policy on unexpected decisions.
_STAGE1_ROUTES: Mapping[str, str] = {
    "run_stage2": "stage2",
    "run_calculation_planner": "calculation_planner",
    "request_clarification": "clarify",
    "unanswerable": "unanswerable",
    "fail_closed": "fail_closed",
}
_STAGE2_ROUTES: Mapping[str, str] = {
    "run_stage3": "stage3",
    "retry_search": "retry_search",
    "unanswerable": "unanswerable",
    "fail_closed": "fail_closed",
    "request_clarification": "clarify",
}
_STAGE3_ROUTES: Mapping[str, str] = {
    "run_stage4": "stage4",
    "run_calculation_planner": "calculation_planner",
    "fail_closed": "fail_closed",
    "request_clarification": "clarify",
    "unanswerable": "unanswerable",
}
_STAGE4_ROUTES: Mapping[str, str] = {
    "regenerate_answer": "answer_regeneration",
    "finish": END,
    # After Stage4, fail-closed terminates directly instead of looping back
    # through the "fail_closed" node (which itself re-enters Stage4).
    "fail_closed": END,
}
_PHASE_ROUTES: Mapping[str, Mapping[str, str]] = {
    "after_stage1": _STAGE1_ROUTES,
    "after_stage2": _STAGE2_ROUTES,
    "after_stage3": _STAGE3_ROUTES,
    "after_stage4": _STAGE4_ROUTES,
}


def _supervisor_route(state: Mapping[str, Any]) -> str:
    phase = str(state.get("supervisor_phase") or state.get("phase") or "after_stage1")
    action = str(state.get("supervisor_action", "fail_closed"))
    routes = _PHASE_ROUTES.get(phase, _STAGE1_ROUTES)
    return routes.get(action, "fail_closed")


def _blocked_route(route: str) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    reason = {
        "need_clarify": "need_clarify",
        "unanswerable": "unanswerable",
        "unsafe": "unsafe",
    }.get(route, "validation_failed")
    return lambda _state: {"route": route, "termination_reason": reason}


def _fail_closed(state: Mapping[str, Any]) -> dict[str, Any]:
    """Terminate safely without corrupting the user's original route."""
    route = str(state.get("route") or "unanswerable")
    reason = "unsafe" if route == "unsafe" else "validation_failed"
    return {"termination_reason": reason}


def _stage_node(owner: str, phase: str, node: StateNode) -> StateNode:
    """Wrap a Stage node with its ownership and phase-advance hooks.

    Both hooks run as middleware around the Stage's own callable so they take
    effect without adding separate boxes to the compiled graph: the graph
    only ever sees a single "stage1".."stage4" node.
    """

    def wrapped(state: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_node_update(owner, state, node(state))
        return {**validated, "supervisor_phase": phase, "phase": phase}

    return wrapped


def build_graph(nodes: StageNodes):
    """Build the bounded Supervisor-controlled four-stage graph."""

    supervisor = nodes.supervisor or build_supervisor_node()
    planner = nodes.calculation_planner or build_planner_tool()
    regeneration = nodes.answer_regeneration or (lambda state: {
        "answer": str(state.get("answer") or ""),
        "regeneration_attempts": int(state.get("regeneration_attempts", 0) or 0) + 1,
    })
    builder = StateGraph(AgentState)
    builder.add_node("stage1", _stage_node("stage1", "after_stage1", nodes.stage1))
    builder.add_node("stage2", _stage_node("stage2", "after_stage2", nodes.stage2))
    builder.add_node("stage3", _stage_node("stage3", "after_stage3", nodes.stage3))
    builder.add_node("stage4", _stage_node("stage4", "after_stage4", nodes.stage4))
    builder.add_node("calculation_planner", planner)
    builder.add_node("retry_search", retry_search_tool)
    builder.add_node("supervisor", supervisor)
    builder.add_node("answer_regeneration", regeneration)
    builder.add_node("clarify", _blocked_route("need_clarify"))
    builder.add_node("unanswerable", _blocked_route("unanswerable"))
    builder.add_node("fail_closed", _fail_closed)

    builder.add_edge(START, "stage1")
    builder.add_edge("stage1", "supervisor")
    builder.add_edge("stage2", "supervisor")
    builder.add_edge("stage3", "supervisor")
    builder.add_edge("stage4", "supervisor")
    builder.add_edge("calculation_planner", "supervisor")
    builder.add_edge("retry_search", "stage2")
    builder.add_edge("clarify", "stage4")
    builder.add_edge("unanswerable", "stage4")
    builder.add_edge("fail_closed", "stage4")
    builder.add_edge("answer_regeneration", "stage4")

    builder.add_conditional_edges(
        "supervisor",
        _supervisor_route,
        {
            "stage2": "stage2",
            "stage3": "stage3",
            "stage4": "stage4",
            "calculation_planner": "calculation_planner",
            "retry_search": "retry_search",
            "clarify": "clarify",
            "unanswerable": "unanswerable",
            "fail_closed": "fail_closed",
            "answer_regeneration": "answer_regeneration",
            END: END,
        },
    )
    return builder.compile()


__all__ = ["StageNodes", "StateNode", "build_graph"]
