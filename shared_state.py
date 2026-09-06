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
SupervisorPhase = Literal["after_interpreter", "after_retriever", "after_reasoner", "after_validator"]
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


class InterpreterCalculationPlan(TypedDict, total=False):
    """Canonical calculation plan optionally produced by Interpreter."""

    operation: str
    metric: str
    denominator_metric: str


class InterpreterIntent(TypedDict, total=False):
    """JSON-compatible Interpreter Intent payload stored in ``AgentState``."""

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
    calculation: InterpreterCalculationPlan
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
    llm_status: str
    interpretation_uncertain: bool
    aggregation_scope: str
    think_trace: str
    query_plan: list[dict[str, Any]]


class RetrieverDocument(TypedDict, total=False):
    """Minimum structured evidence document passed from Retriever."""

    id: str
    doc_id: str
    document_id: str
    chunk_id: str
    source: str
    text: str
    metadata: dict[str, Any]
    evidence_spans: list[dict[str, Any]]


class RetrieverResult(TypedDict, total=False):
    """Structured Retriever handoff consumed by Reasoner."""

    query_id: str
    documents: list[RetrieverDocument]
    cited_documents: list[RetrieverDocument]
    retrieval_trace: list[Any]
    status: str
    subresults: list[dict[str, Any]]
    warnings: list[str]
    search_queries: dict[str, str]
    provider_status: dict[str, Any]


class ReasonerResult(TypedDict, total=False):
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


class ValidatorResult(TypedDict, total=False):
    """Structured validation and regeneration result produced by Validator."""

    status: str
    answer: str
    numeric_check: dict[str, Any]
    citation_check: dict[str, Any]
    semantic_check: dict[str, Any]
    regenerated: bool
    warnings: list[str]
    trace: list[str]
    provider_status: dict[str, Any]
    failure_reason_code: str | None


class AgentState(MessagesState):
    """Full State shared by the external Interpreter~Validator integration graph.

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

    # Interpreter
    intent: dict[str, Any] | None
    route: Route | None
    analysis_plan: dict[str, Any] | None
    plan_status: str | None
    plan_failure_reason: str | None
    plan_trace: list[str]

    # Retriever
    retriever_result: dict[str, Any] | None
    documents: list[dict[str, Any]] | None
    search_queries: dict[str, str]

    # Reasoner
    reasoner_result: dict[str, Any] | None
    facts: list[dict[str, Any]] | None

    # Validator
    validator_result: dict[str, Any] | None

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
    reinterpretation_attempts: int
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
    analysis_plan: dict[str, Any] | None
    plan_status: str | None
    plan_failure_reason: str | None
    plan_trace: list[str]
    retriever_result: dict[str, Any] | None
    documents: list[dict[str, Any]] | None
    search_queries: dict[str, str]
    reasoner_result: dict[str, Any] | None
    facts: list[dict[str, Any]] | None
    validator_result: dict[str, Any] | None
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
    reinterpretation_attempts: int
    validation_attempts: int
    termination_reason: str | None
    original_question: str
    search_query: str


IMMUTABLE_STATE_FIELDS = frozenset({"question_id", "question"})

STAGE_WRITE_FIELDS: Mapping[str, frozenset[str]] = {
    "interpreter": frozenset({"intent", "route", "search_query"}),
    "retriever": frozenset({"retriever_result", "retry_num", "search_attempts", "documents", "search_query", "search_queries"}),
    "reasoner": frozenset({"reasoner_result", "answer", "context", "messages", "gen_retry_num", "facts"}),
    "validator": frozenset({"validator_result", "answer", "messages", "validation_attempts"}),
    "planner": frozenset({
        "analysis_plan", "plan_status", "plan_failure_reason", "plan_trace",
        "planner_retry_num", "planner_attempts",
    }),
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
        "analysis_plan": None,
        "plan_status": None,
        "plan_failure_reason": None,
        "plan_trace": [],
        "retriever_result": None,
        "documents": None,
        "search_queries": {},
        "reasoner_result": None,
        "facts": None,
        "validator_result": None,
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
        "reinterpretation_attempts": 0,
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
    "InterpreterCalculationPlan",
    "InterpreterIntent",
    "RetrieverDocument",
    "RetrieverResult",
    "ReasonerResult",
    "ValidatorResult",
    "Route",
    "SupervisorPhase",
    "TerminationReason",
    "make_initial_agent_state",
    "validate_node_update",
]
