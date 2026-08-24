from __future__ import annotations

from typing import Any, Annotated, TypedDict


def append_values(left: list[Any], right: list[Any]) -> list[Any]:
    return [*left, *right]


class AgenticState(TypedDict, total=False):
    question_id: str
    question: str
    normalized_question: str
    intent: str
    intent_confidence: float
    metadata: dict[str, Any]
    selected_agent: str
    retrieved_documents: list[dict[str, Any]]
    cited_documents: list[dict[str, Any]]
    alternative_documents: dict[str, list[dict[str, Any]]]
    facts: list[dict[str, Any]]
    calculations: dict[str, Any]
    calculation_plan: dict[str, Any]
    comparison_results: list[dict[str, Any]]
    comparison_targets: list[str]
    parallel_documents: Annotated[list[dict[str, Any]], append_values]
    parallel_failures: Annotated[list[dict[str, str]], append_values]
    comparison_target: str
    linked_events: list[dict[str, Any]]
    agent_results: Annotated[list[dict[str, Any]], append_values]
    provenance: Annotated[list[dict[str, Any]], append_values]
    messages: Annotated[list[dict[str, Any]], append_values]
    handoffs: Annotated[list[dict[str, Any]], append_values]
    validation: dict[str, Any]
    answer: str
    fallback_reason: str
    status: str
    error: str
    trace: Annotated[list[str], append_values]
