from __future__ import annotations

from typing import Any, Annotated, Mapping, TypedDict

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
    stage3_result: dict[str, Any]


class Stage3NodeState(TypedDict, total=False):
    """Structural input contract for the single Stage3 LangGraph node.

    The outer application may use a richer ``AgentState``.  Stage3 only
    depends on these keys and therefore does not import the team's concrete
    state module.
    """

    question: str
    intent: Mapping[str, Any] | Stage3Intent
    route: str
    stage2_result: Any
    context: str
    messages: list[Any]
    gen_retry_num: int


class Stage3NodeOutput(TypedDict, total=False):
    """Partial state update returned by the canonical Stage3 node."""

    answer: str
    context: str
    messages: list[Any]
    gen_retry_num: int
    stage3_result: dict[str, Any]


class Stage3NodeGraphState(Stage3NodeState, Stage3NodeOutput, total=False):
    """Combined schema used only by the optional one-node compatibility graph."""


__all__ = [
    "Stage3GraphState",
    "Stage3NodeGraphState",
    "Stage3NodeOutput",
    "Stage3NodeState",
    "append_values",
]
