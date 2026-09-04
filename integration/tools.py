"""Typed Supervisor tools and the native LangGraph ToolNode boundary."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from langgraph.prebuilt import ToolNode

from stage3.deterministic.calculation_planner import build_simple_analysis_plan


@tool("calculation_planner")
def calculation_planner_tool(
    operation: str,
    metric: str = "",
    denominator_metric: str = "",
) -> dict[str, Any]:
    """Return a bounded, structured calculation plan."""

    return {
        "analysis_plan": build_simple_analysis_plan(
            operation.strip(), metric.strip(), denominator_metric.strip()
        ),
        "plan_status": "ready",
    }


@tool("retry_search")
def retry_search_tool(query: str, original_question: str) -> dict[str, Any]:
    """Create a changed retrieval query while preserving the original question."""

    return {
        "search_query": f"{query.strip()} 수치 기간 기준 출처",
        "original_question": original_question,
    }


@tool("answer_regeneration")
def answer_regeneration_tool(answer: str) -> dict[str, Any]:
    """Typed boundary for the externally injected answer regeneration client."""

    return {"answer": answer}


def build_supervisor_tool_node() -> ToolNode:
    """Build the native ToolNode with only allow-listed Supervisor tools."""

    return ToolNode([calculation_planner_tool, retry_search_tool, answer_regeneration_tool])


__all__ = [
    "answer_regeneration_tool",
    "build_supervisor_tool_node",
    "calculation_planner_tool",
    "retry_search_tool",
]
