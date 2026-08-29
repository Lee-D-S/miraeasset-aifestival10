from __future__ import annotations

from typing import Any

from stage3.agents.calculation import calculation_agent
from stage3.agents.comparison import comparison_agent
from stage3.agents.event_linker import event_linker_agent
from stage3.agents.answer import AnswerWriter
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
    validation_node,
)
from stage3.orchestration.router import analysis_targets, route_after_gate, route_after_validation
from stage3.state import Stage3GraphState


def build_graph(*, answer_writer: AnswerWriter):
    """Build the optional LangGraph Stage3 workflow.

    Importing this module is safe without LangGraph; the dependency is loaded
    only when the graph is explicitly built.
    """

    try:
        from langgraph.graph import END, START, StateGraph
        from langgraph.types import Send
    except ImportError as error:  # pragma: no cover - exercised without optional dependency
        raise RuntimeError("LangGraph 실행에는 langgraph 패키지가 필요합니다.") from error

    builder = StateGraph(Stage3GraphState)
    builder.add_node("stage1_gate", stage1_gate_node)
    builder.add_node("blocked_response", blocked_response_node)
    builder.add_node("stage2_adapter", stage2_adapter_node)
    builder.add_node("fact_extraction_agent", fact_extraction_node)
    builder.add_node("analysis_router", analysis_router_node)
    builder.add_node("calculation_agent", make_specialist_node(calculation_agent, "calculations"))
    builder.add_node("comparison_agent", make_specialist_node(comparison_agent, "comparison_results"))
    builder.add_node("event_linker_agent", make_specialist_node(event_linker_agent, "linked_events"))
    builder.add_node("merge_analysis", merge_analysis_node)
    builder.add_node("answer_agent", answer_node(answer_writer))
    builder.add_node("validation_agent", validation_node)
    builder.add_node("fallback_agent", fallback_node(answer_writer))
    builder.add_node("final_failure", final_failure_node)

    builder.add_edge(START, "stage1_gate")
    builder.add_conditional_edges(
        "stage1_gate",
        route_after_gate,
        {"blocked": "blocked_response", "stage2_adapter": "stage2_adapter"},
    )
    builder.add_edge("blocked_response", END)
    builder.add_edge("stage2_adapter", "fact_extraction_agent")
    builder.add_edge("fact_extraction_agent", "analysis_router")

    def route_analysis(state: dict[str, Any]):
        targets = analysis_targets(state["intent"])
        if not targets:
            return "merge_analysis"
        return [Send(target, dict(state)) for target in targets]

    builder.add_conditional_edges(
        "analysis_router",
        route_analysis,
        {
            "merge_analysis": "merge_analysis",
            "calculation_agent": "calculation_agent",
            "comparison_agent": "comparison_agent",
            "event_linker_agent": "event_linker_agent",
        },
    )
    for node_name in ("calculation_agent", "comparison_agent", "event_linker_agent"):
        builder.add_edge(node_name, "merge_analysis")
    builder.add_edge("merge_analysis", "answer_agent")
    builder.add_edge("answer_agent", "validation_agent")
    builder.add_conditional_edges(
        "validation_agent",
        route_after_validation,
        {"completed": END, "fallback": "fallback_agent", "failed": "final_failure"},
    )
    builder.add_edge("fallback_agent", "validation_agent")
    builder.add_edge("final_failure", END)
    return builder.compile()


__all__ = ["build_graph"]
