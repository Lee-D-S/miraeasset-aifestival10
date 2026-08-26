from __future__ import annotations

from typing import Any

from stage3.agents.answer import AnswerWriter
from stage3.agents.calculation import calculation_agent
from stage3.agents.comparison import comparison_agent
from stage3.agents.event_linker import event_linker_agent
from stage3.contracts import Stage3Intent
from stage3.orchestration.nodes import (
    analysis_router_node,
    answer_node,
    blocked_response_node,
    fact_extraction_node,
    fallback_node,
    final_failure_node,
    make_specialist_node,
    merge_analysis_node,
    stage1_gate_node,
    stage2_adapter_node,
    state_to_result,
    validation_node,
)
from stage3.orchestration.router import analysis_targets, route_after_gate, route_after_validation
from stage3.state import Stage3GraphState


def _apply(state: Stage3GraphState, update: dict[str, Any]) -> None:
    append_keys = {"warnings", "agent_results", "provenance", "handoffs", "trace"}
    for key, value in update.items():
        if key in append_keys:
            state[key] = [*state.get(key, []), *value]
        else:
            state[key] = value


def run_stdlib(
    *,
    question: str,
    intent: Stage3Intent,
    stage2_result: Any,
    answer_writer: AnswerWriter,
) -> Stage3GraphState:
    state: Stage3GraphState = {
        "question": question,
        "intent": intent,
        "stage2_result": stage2_result,
        "warnings": [],
        "agent_results": [],
        "provenance": [],
        "handoffs": [],
        "trace": [],
        "fallback_used": False,
        "validation_warnings": [],
    }
    _apply(state, stage1_gate_node(state))
    if route_after_gate(state) == "blocked":
        _apply(state, blocked_response_node(state))
        return state

    _apply(state, stage2_adapter_node(state))
    _apply(state, fact_extraction_node(state))
    _apply(state, analysis_router_node(state))
    handlers = {
        "calculation_agent": make_specialist_node(calculation_agent, "calculations"),
        "comparison_agent": make_specialist_node(comparison_agent, "comparison_results"),
        "event_linker_agent": make_specialist_node(event_linker_agent, "linked_events"),
    }
    for target in analysis_targets(intent):
        _apply(state, handlers[target](state))
    _apply(state, merge_analysis_node(state))
    _apply(state, answer_node(answer_writer)(state))
    _apply(state, validation_node(state))
    if route_after_validation(state) == "fallback":
        _apply(state, fallback_node(answer_writer)(state))
        _apply(state, validation_node(state))
    if route_after_validation(state) == "failed":
        _apply(state, final_failure_node(state))
    return state


__all__ = ["run_stdlib"]
