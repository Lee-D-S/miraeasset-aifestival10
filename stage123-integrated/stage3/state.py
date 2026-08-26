from __future__ import annotations

from typing import Any, Annotated, TypedDict

from stage3.contracts import Stage3Document, Stage3Fact, Stage3Intent


def append_values(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer used by LangGraph for append-only execution metadata."""

    return [*left, *right]


class Stage3GraphState(TypedDict, total=False):
    question: str
    intent: Stage3Intent
    stage2_result: Any
    documents: list[Stage3Document]
    facts: list[Stage3Fact]
    calculations: list[dict[str, Any]]
    comparison_results: list[dict[str, Any]]
    linked_events: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    warnings: Annotated[list[str], append_values]
    agent_results: Annotated[list[dict[str, Any]], append_values]
    provenance: Annotated[list[dict[str, Any]], append_values]
    handoffs: Annotated[list[dict[str, Any]], append_values]
    trace: Annotated[list[str], append_values]
    answer: str
    answer_mode: str
    status: str
    fallback_reason: str
    fallback_used: bool
    validation_warnings: list[str]
    validation: dict[str, Any]


__all__ = ["Stage3GraphState", "append_values"]
