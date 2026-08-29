from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Intent = Literal["lookup", "comparison", "calculation", "event_link", "fact_extraction", "unsupported"]


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "answer": self.answer,
            "facts": list(self.facts),
            "calculations": dict(self.calculations),
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class Provenance:
    agent: str
    question: str
    document_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    confidence: float = 0.0
    details: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "question": self.question,
            "document_ids": list(self.document_ids),
            "sources": list(self.sources),
            "confidence": self.confidence,
            "details": self.details or {},
        }
