from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


Stage3Route = Literal["ok", "need_clarify", "unanswerable", "unsafe"]


@dataclass(frozen=True)
class Stage3Document:
    """Stage2 document shape consumed by Stage3."""

    id: str
    source: str
    text: str
    score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_spans: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "text": self.text,
            "score": self.score,
            "metadata": dict(self.metadata),
            "evidence_spans": [dict(span) for span in self.evidence_spans],
        }


@dataclass(frozen=True)
class Stage2Bundle:
    """Normalized Stage2 output, independent of the final upstream schema."""

    documents: list[Stage3Document] = field(default_factory=list)
    cited_documents: list[Stage3Document] = field(default_factory=list)
    retrieval_trace: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def effective_documents(self) -> list[Stage3Document]:
        return list(self.cited_documents or self.documents)

    def to_dict(self) -> dict[str, Any]:
        return {
            "documents": [document.to_dict() for document in self.documents],
            "cited_documents": [document.to_dict() for document in self.cited_documents],
            "retrieval_trace": list(self.retrieval_trace),
        }


@dataclass(frozen=True)
class Stage3Fact:
    metric: str
    label: str
    value: float | str
    raw_value: float | str
    unit: str
    normalized_value: float | str | None
    period: str | None
    basis: str | None
    company: str | None
    document_id: str
    source: str
    evidence: str
    span_start: int | None = None
    span_end: int | None = None
    confidence: float = 0.0
    kind: str = "numeric"

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "label": self.label,
            "value": self.value,
            "raw_value": self.raw_value,
            "unit": self.unit,
            "normalized_value": self.normalized_value,
            "period": self.period,
            "basis": self.basis,
            "company": self.company,
            "document_id": self.document_id,
            "source": self.source,
            "evidence": self.evidence,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "confidence": self.confidence,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class Stage3Result:
    status: str
    answer: str = ""
    facts: list[dict[str, Any]] = field(default_factory=list)
    calculations: list[dict[str, Any]] = field(default_factory=list)
    comparison_results: list[dict[str, Any]] = field(default_factory=list)
    linked_events: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    provenance: list[dict[str, Any]] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "answer": self.answer,
            "facts": list(self.facts),
            "calculations": list(self.calculations),
            "comparison_results": list(self.comparison_results),
            "linked_events": list(self.linked_events),
            "citations": list(self.citations),
            "warnings": list(self.warnings),
            "provenance": list(self.provenance),
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class Stage3Intent:
    """Normalized contract consumed by the Stage3 pipeline."""

    question: str
    normalized_question: str
    route: Stage3Route
    intent: str
    metric: str | None = None
    basis: str | None = None
    time: dict[str, Any] = field(default_factory=dict)
    correction_mode: str | None = None
    manifest_filter: dict[str, Any] = field(default_factory=dict)
    companies: list[str] = field(default_factory=list)
    sector: str | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_processable(self) -> bool:
        return self.route == "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "normalized_question": self.normalized_question,
            "route": self.route,
            "intent": self.intent,
            "metric": self.metric,
            "basis": self.basis,
            "time": dict(self.time),
            "correction_mode": self.correction_mode,
            "manifest_filter": dict(self.manifest_filter),
            "companies": list(self.companies),
            "sector": self.sector,
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
        }


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def adapt_stage1_intent(intent: Mapping[str, Any], *, question: str | None = None) -> Stage3Intent:
    """Convert a Stage1 Intent dictionary without re-classifying the question."""

    source = dict(intent)
    raw_question = str(question if question is not None else source.get("raw_question", source.get("question", "")))
    normalized = str(source.get("normalized_question", raw_question)).strip()
    route = str(source.get("route", "unanswerable"))
    if route not in {"ok", "need_clarify", "unanswerable", "unsafe"}:
        route = "unanswerable"

    corp_entries = source.get("corps", source.get("companies", []))
    companies: list[str] = []
    if isinstance(corp_entries, list):
        for item in corp_entries:
            value = item.get("corp_name", item.get("name", "")) if isinstance(item, Mapping) else item
            if str(value).strip():
                companies.append(str(value))

    metric = source.get("metric")
    basis = source.get("basis")
    return Stage3Intent(
        question=raw_question,
        normalized_question=normalized,
        route=route,  # type: ignore[arg-type]
        intent=str(source.get("intent", "unsupported")),
        metric=str(metric).strip() if metric is not None and str(metric).strip() else None,
        basis=str(basis).strip() if basis is not None and str(basis).strip() else None,
        time=_mapping(source.get("time")),
        correction_mode=str(source["correction_mode"]) if source.get("correction_mode") is not None else None,
        manifest_filter=_mapping(source.get("manifest_filter")),
        companies=companies,
        sector=str(source["sector"]) if source.get("sector") is not None else None,
        assumptions=_list_of_strings(source.get("assumptions")),
        warnings=_list_of_strings(source.get("warnings")),
        source=source,
    )
