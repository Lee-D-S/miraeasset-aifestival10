"""LangGraph composition for the shared Interpreter~Validator execution State."""

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

    interpreter: StateNode
    retriever: StateNode
    reasoner: StateNode
    validator: StateNode
    supervisor: StateNode | None = None
    calculation_planner: StateNode | None = None
    answer_regeneration: StateNode | None = None
    reinterpreter: StateNode | None = None


# One route table per Supervisor phase: action -> next node (or END). A
# missing action for a phase fails closed, mirroring the Supervisor's own
# fail-closed policy on unexpected decisions.
_INTERPRETER_ROUTES: Mapping[str, str] = {
    "run_retriever": "retriever",
    "run_calculation_planner": "calculation_planner",
    "request_clarification": "clarify",
    "unanswerable": "unanswerable",
    "fail_closed": "fail_closed",
}
_RETRIEVER_ROUTES: Mapping[str, str] = {
    "run_reasoner": "reasoner",
    "retry_search": "retry_search",
    "unanswerable": "unanswerable",
    "fail_closed": "fail_closed",
    "request_clarification": "clarify",
}
_REASONER_ROUTES: Mapping[str, str] = {
    "reinterpret_question": "reinterpret_question",
    "run_validator": "validator",
    "run_calculation_planner": "calculation_planner",
    "fail_closed": "fail_closed",
    "request_clarification": "clarify",
    "unanswerable": "unanswerable",
}
_VALIDATOR_ROUTES: Mapping[str, str] = {
    "regenerate_answer": "answer_regeneration",
    "finish": END,
    # After Validator, fail-closed terminates directly instead of looping back
    # through the "fail_closed" node (which itself re-enters Validator).
    "fail_closed": END,
}
_PHASE_ROUTES: Mapping[str, Mapping[str, str]] = {
    "after_interpreter": _INTERPRETER_ROUTES,
    "after_retriever": _RETRIEVER_ROUTES,
    "after_reasoner": _REASONER_ROUTES,
    "after_validator": _VALIDATOR_ROUTES,
}


def _supervisor_route(state: Mapping[str, Any]) -> str:
    phase = str(state.get("supervisor_phase") or state.get("phase") or "after_interpreter")
    action = str(state.get("supervisor_action", "fail_closed"))
    routes = _PHASE_ROUTES.get(phase, _INTERPRETER_ROUTES)
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
    only ever sees a single "interpreter".."validator" node.
    """

    def wrapped(state: Mapping[str, Any]) -> dict[str, Any]:
        validated = validate_node_update(owner, state, node(state))
        return {**validated, "supervisor_phase": phase, "phase": phase}

    return wrapped


def _planner_node(node: StateNode) -> StateNode:
    """Apply planner ownership validation without changing the Supervisor phase."""

    def wrapped(state: Mapping[str, Any]) -> dict[str, Any]:
        return validate_node_update("planner", state, node(state))

    return wrapped


def _reinterpret_node(node: StateNode) -> StateNode:
    def wrapped(state: Mapping[str, Any]) -> dict[str, Any]:
        attempts = int(state.get("reinterpretation_attempts", 0) or 0)
        if attempts >= 1:
            return {"route": "need_clarify", "supervisor_phase": "after_interpreter"}
        update = validate_node_update("interpreter", state, node({**state, "reinterpretation_attempts": attempts + 1}))
        return {
            **update, "reinterpretation_attempts": attempts + 1,
            "supervisor_phase": "after_interpreter", "phase": "after_interpreter",
            "analysis_plan": None, "plan_status": None, "plan_failure_reason": None, "plan_trace": [],
            "planner_attempts": 0, "planner_retry_num": 0,
            "retriever_result": None, "documents": None, "search_queries": {},
            "reasoner_result": None, "facts": None, "answer": None, "context": None,
            "validator_result": None,
            "search_query": str(state.get("original_question") or state.get("question", "")),
        }
    return wrapped


def build_graph(nodes: StageNodes):
    """Build the bounded Supervisor-controlled four-stage graph."""

    supervisor = nodes.supervisor or build_supervisor_node(
        allow_regeneration=nodes.answer_regeneration is not None,
        allow_reinterpretation=nodes.reinterpreter is not None,
    )
    planner = nodes.calculation_planner or build_planner_tool()
    regeneration = nodes.answer_regeneration or (lambda state: {
        # The built-in Supervisor does not route here without a live answer
        # client. Keep a non-no-op fail-closed update for custom Supervisors.
        "answer": "",
        "regeneration_attempts": int(state.get("regeneration_attempts", 0) or 0) + 1,
    })
    builder = StateGraph(AgentState)
    builder.add_node("interpreter", _stage_node("interpreter", "after_interpreter", nodes.interpreter))
    builder.add_node("reinterpret_question", _reinterpret_node(nodes.reinterpreter or nodes.interpreter))
    builder.add_node("retriever", _stage_node("retriever", "after_retriever", nodes.retriever))
    builder.add_node("reasoner", _stage_node("reasoner", "after_reasoner", nodes.reasoner))
    builder.add_node("validator", _stage_node("validator", "after_validator", nodes.validator))
    builder.add_node("calculation_planner", _planner_node(planner))
    builder.add_node("retry_search", retry_search_tool)
    builder.add_node("supervisor", supervisor)
    builder.add_node("answer_regeneration", regeneration)
    builder.add_node("clarify", _blocked_route("need_clarify"))
    builder.add_node("unanswerable", _blocked_route("unanswerable"))
    builder.add_node("fail_closed", _fail_closed)

    builder.add_edge(START, "interpreter")
    builder.add_edge("interpreter", "supervisor")
    builder.add_edge("reinterpret_question", "supervisor")
    builder.add_edge("retriever", "supervisor")
    builder.add_edge("reasoner", "supervisor")
    builder.add_edge("validator", "supervisor")
    builder.add_edge("calculation_planner", "supervisor")
    builder.add_edge("retry_search", "retriever")
    builder.add_edge("clarify", "validator")
    builder.add_edge("unanswerable", "validator")
    builder.add_edge("fail_closed", "validator")
    builder.add_edge("answer_regeneration", "validator")

    builder.add_conditional_edges(
        "supervisor",
        _supervisor_route,
        {
            "retriever": "retriever",
            "reinterpret_question": "reinterpret_question",
            "reasoner": "reasoner",
            "validator": "validator",
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
