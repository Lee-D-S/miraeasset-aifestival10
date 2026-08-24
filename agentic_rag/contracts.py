from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

Intent = Literal["lookup", "comparison", "calculation", "event_link", "fact_extraction", "unsupported"]


class AgenticState(TypedDict, total=False):
    question_id: str
    question: str
    normalized_question: str
    intent: Intent
    intent_confidence: float
    metadata: dict[str, str]
    selected_agent: str
    retrieved_documents: list[dict[str, Any]]
    cited_documents: list[dict[str, Any]]
    facts: list[dict[str, Any]]
    calculations: dict[str, Any]
    answer: str
    fallback_reason: str
    handoffs: list[dict[str, Any]]
    trace: list[str]
    status: str
    error: str


@dataclass(frozen=True)
class HandoffRequest:
    target: str
    task: str
    reason: str
    evidence: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()


@dataclass(frozen=True)
class AgentResult:
    agent: str
    status: str
    answer: str = ""
    facts: tuple[dict[str, Any], ...] = ()
    calculations: dict[str, Any] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    trace: tuple[str, ...] = ()

