"""Shared execution state for the four-stage LangGraph pipeline.

This module owns the cross-stage state contract.  Individual Stage packages
may define narrower input/output TypedDicts, but the outer integration graph
should use :class:`AgentState` and merge each node's partial update into it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Annotated, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph import MessagesState, add_messages


Route = Literal["ok", "need_clarify", "unanswerable", "unsafe"]
SupervisorPhase = Literal["after_stage1", "after_stage2", "after_stage3", "after_stage4"]
TerminationReason = Literal[
    "unsafe",
    "unanswerable",
    "need_clarify",
    "validation_failed",
    "configuration_failure",
    "supervisor_error",
    "supervisor_limit",
    "completed",
]


class Stage1CalculationPlan(TypedDict, total=False):
    """Canonical calculation plan optionally produced by Stage1."""

    operation: str
    metric: str
    denominator_metric: str


class Stage1Intent(TypedDict, total=False):
    """JSON-compatible Stage1 Intent payload stored in ``AgentState``."""

    raw_question: str
    normalized_question: str
    intent: str
    question_type: str
    route: Route
    corps: list[dict[str, Any]]
    excluded_corps: list[dict[str, Any]]
    sector: str | None
    sector_members: list[str]
    ambiguous_mentions: list[str]
    unknown_entities: list[str]
    related_entities: list[str]
    metric: str
    metric_confidence: str
    basis: str
    time: dict[str, Any]
    calculation: Stage1CalculationPlan
    correction_mode: str
    allow_pdf_html: bool
    manifest_filter: dict[str, Any]
    doc_count: int
    availability: str
    assumptions: list[str]
    warnings: list[str]
    missing_slots: list[str]
    reject_reason: str | None
    clarify_message: str | None
    llm_used: bool
    think_trace: str
    query_plan: list[dict[str, Any]]


class Stage2Document(TypedDict, total=False):
    """Minimum structured evidence document passed from Stage2."""

    id: str
    doc_id: str
    document_id: str
    chunk_id: str
    source: str
    text: str
    metadata: dict[str, Any]
    evidence_spans: list[dict[str, Any]]


class Stage2Result(TypedDict, total=False):
    """Structured Stage2 handoff consumed by Stage3."""

    query_id: str
    documents: list[Stage2Document]
    cited_documents: list[Stage2Document]
    retrieval_trace: list[Any]
    status: str
    subresults: list[dict[str, Any]]
    warnings: list[str]
    search_queries: dict[str, str]
    provider_status: dict[str, Any]


class Stage3Result(TypedDict, total=False):
    """Structured Fact, calculation, citation, and answer draft result."""

    status: str
    answer: str
    facts: list[dict[str, Any]]
    calculations: list[dict[str, Any]]
    comparison_results: list[dict[str, Any]]
    linked_events: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    warnings: list[str]
    trace: list[str]
    subresults: list[dict[str, Any]]


class Stage4Result(TypedDict, total=False):
    """Structured validation and regeneration result produced by Stage4."""

    status: str
    answer: str
    numeric_check: dict[str, Any]
    citation_check: dict[str, Any]
    semantic_check: dict[str, Any]
    regenerated: bool
    warnings: list[str]
    trace: list[str]
    provider_status: dict[str, Any]


class AgentState(MessagesState):
    """Full State shared by the external Stage1~Stage4 integration graph.

    ``question_id`` and ``question`` are required at the public execution
    boundary and immutable after initialization.  The remaining fields are
    updated by their owning Stage through partial updates.
    """

    # Request identity and immutable input
    question_id: str
    question: str

    # Current answer/context
    answer: str | None
    context: str | None

    # Stage1
    intent: dict[str, Any] | None
    route: Route | None

    # Stage2
    stage2_result: dict[str, Any] | None
    documents: list[dict[str, Any]] | None

    # Stage3
    stage3_result: dict[str, Any] | None
    facts: list[dict[str, Any]] | None

    # Stage4
    stage4_result: dict[str, Any] | None

    # Control counters
    retry_num: int
    gen_retry_num: int
    supervisor_steps: int
    planner_retry_num: int
    supervisor_phase: str | None
    supervisor_action: str | None
    supervisor_reason: str | None
    # Canonical control envelope and execution diagnostics
    phase: str | None
    next_action: str | None
    last_action: str | None
    search_attempts: int
    planner_attempts: int
    regeneration_attempts: int
    validation_attempts: int
    termination_reason: str | None
    original_question: str
    search_query: str


class AgentStateUpdate(TypedDict, total=False):
    """Partial update accepted from a Stage node.

    ``question_id`` and ``question`` are deliberately absent: nodes must not
    change the original request.  ``messages`` uses the same reducer as
    ``MessagesState`` so a node appends messages instead of replacing history.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    answer: str | None
    context: str | None
    intent: dict[str, Any] | None
    route: Route | None
    stage2_result: dict[str, Any] | None
    documents: list[dict[str, Any]] | None
    stage3_result: dict[str, Any] | None
    facts: list[dict[str, Any]] | None
    stage4_result: dict[str, Any] | None
    retry_num: int
    gen_retry_num: int
    supervisor_steps: int
    planner_retry_num: int
    supervisor_phase: str | None
    supervisor_action: str | None
    supervisor_reason: str | None
    phase: str | None
    next_action: str | None
    last_action: str | None
    search_attempts: int
    planner_attempts: int
    regeneration_attempts: int
    validation_attempts: int
    termination_reason: str | None
    original_question: str
    search_query: str


IMMUTABLE_STATE_FIELDS = frozenset({"question_id", "question"})

STAGE_WRITE_FIELDS: Mapping[str, frozenset[str]] = {
    "stage1": frozenset({"intent", "route", "search_query"}),
    "stage2": frozenset({"stage2_result", "retry_num", "search_attempts", "documents", "search_query"}),
    "stage3": frozenset({"stage3_result", "answer", "context", "messages", "gen_retry_num", "facts"}),
    "stage4": frozenset({"stage4_result", "answer", "messages", "validation_attempts"}),
}


def validate_node_update(
    owner: str,
    state: Mapping[str, Any],
    update: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate a stage's partial update before LangGraph merges it.

    The graph passes only partial updates, so immutable request fields are
    rejected if supplied and each stage is restricted to its ownership set.
    """

    allowed = STAGE_WRITE_FIELDS.get(owner)
    if allowed is None:
        raise ValueError(f"unknown stage owner: {owner}")
    if not isinstance(update, Mapping):
        raise TypeError(f"{owner} node must return a mapping")
    illegal = set(update) - allowed
    if illegal & IMMUTABLE_STATE_FIELDS:
        raise ValueError(f"immutable state fields cannot be changed: {sorted(illegal & IMMUTABLE_STATE_FIELDS)}")
    if illegal:
        raise ValueError(f"{owner} node wrote unauthorized fields: {sorted(illegal)}")
    if "search_query" in update and not isinstance(update["search_query"], str):
        raise TypeError("search_query must be a string")
    if state.get("original_question") and state.get("question") != state.get("original_question"):
        raise ValueError("question changed during execution")
    return dict(update)


def make_initial_agent_state(
    *,
    question_id: str,
    question: str,
    messages: Sequence[AnyMessage] | None = None,
) -> AgentState:
    """Create the complete initial State at the API/graph boundary.

    The request identity is validated here instead of silently inventing an
    ID.  Each call receives a fresh message list and independent empty state
    values, so callers can safely create multiple runs.
    """

    if not isinstance(question_id, str) or not question_id.strip():
        raise ValueError("question_id는 비어 있지 않은 문자열이어야 합니다.")
    if not isinstance(question, str):
        raise TypeError("question은 문자열이어야 합니다.")

    return {
        "messages": list(messages or []),
        "question_id": question_id,
        "question": question,
        "answer": None,
        "context": None,
        "intent": None,
        "route": None,
        "stage2_result": None,
        "documents": None,
        "stage3_result": None,
        "facts": None,
        "stage4_result": None,
        "retry_num": 0,
        "gen_retry_num": 0,
        "supervisor_steps": 0,
        "planner_retry_num": 0,
        "supervisor_phase": None,
        "supervisor_action": None,
        "supervisor_reason": None,
        "phase": None,
        "next_action": None,
        "last_action": None,
        "search_attempts": 0,
        "planner_attempts": 0,
        "regeneration_attempts": 0,
        "validation_attempts": 0,
        "termination_reason": None,
        "original_question": question,
        "search_query": question,
    }


__all__ = [
    "AgentState",
    "AgentStateUpdate",
    "IMMUTABLE_STATE_FIELDS",
    "STAGE_WRITE_FIELDS",
    "Stage1CalculationPlan",
    "Stage1Intent",
    "Stage2Document",
    "Stage2Result",
    "Stage3Result",
    "Stage4Result",
    "Route",
    "SupervisorPhase",
    "TerminationReason",
    "make_initial_agent_state",
    "validate_node_update",
]
