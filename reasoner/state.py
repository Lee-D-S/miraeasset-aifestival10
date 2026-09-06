from __future__ import annotations

from typing import Any, Annotated, Mapping, TypedDict

from reasoner.contracts import ReasonerDocument, ReasonerFact, ReasonerIntent


def append_values(left: list[Any], right: list[Any]) -> list[Any]:
    """Reducer used by LangGraph for append-only execution metadata."""

    return [*left, *right]


class ReasonerGraphState(TypedDict, total=False):
    question: str
    intent: ReasonerIntent
    retriever_result: Any
    documents: list[ReasonerDocument]
    facts: list[ReasonerFact]
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
    reasoner_result: dict[str, Any]


class ReasonerNodeState(TypedDict, total=False):
    """Structural input contract for the single Reasoner LangGraph node.

    The outer application may use a richer ``AgentState``.  Reasoner only
    depends on these keys and therefore does not import the team's concrete
    state module.
    """

    question: str
    intent: Mapping[str, Any] | ReasonerIntent
    route: str
    retriever_result: Any
    context: str
    messages: list[Any]
    gen_retry_num: int


class ReasonerNodeOutput(TypedDict, total=False):
    """Partial state update returned by the canonical Reasoner node."""

    answer: str
    context: str
    messages: list[Any]
    gen_retry_num: int
    reasoner_result: dict[str, Any]


class ReasonerNodeGraphState(ReasonerNodeState, ReasonerNodeOutput, total=False):
    """Combined schema used only by the optional one-node compatibility graph."""


__all__ = [
    "ReasonerGraphState",
    "ReasonerNodeGraphState",
    "ReasonerNodeOutput",
    "ReasonerNodeState",
    "append_values",
]
