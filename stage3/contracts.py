from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping


Stage3Route = Literal["ok", "need_clarify", "unanswerable", "unsafe"]


@dataclass(frozen=True)
class HandoffRequest:
    """Auditable handoff metadata shared by LangGraph and stdlib runners."""

    source: str
    target: str
    task: str
    reason: str
    evidence: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "task": self.task,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class AgentResult:
    """Structured result returned by every Stage3 Agent node."""

    agent: str
    status: str
    answer: str = ""
    facts: tuple[dict[str, Any], ...] = ()
    calculations: tuple[dict[str, Any], ...] = ()
    comparison_results: tuple[dict[str, Any], ...] = ()
    linked_events: tuple[dict[str, Any], ...] = ()
    evidence_ids: tuple[str, ...] = ()
    confidence: float = 0.0
    warnings: tuple[str, ...] = ()
    trace: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "status": self.status,
            "answer": self.answer,
            "facts": [dict(item) for item in self.facts],
            "calculations": [dict(item) for item in self.calculations],
            "comparison_results": [dict(item) for item in self.comparison_results],
            "linked_events": [dict(item) for item in self.linked_events],
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "warnings": list(self.warnings),
            "trace": list(self.trace),
        }


@dataclass(frozen=True)
class Provenance:
    """Document and execution provenance for one Stage3 Agent."""

    agent: str
    question: str
    document_ids: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    confidence: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "question": self.question,
            "document_ids": list(self.document_ids),
            "sources": list(self.sources),
            "confidence": self.confidence,
            "details": dict(self.details),
        }


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
    currency: str | None = None
    table_context: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Stage3Fact":
        """Rehydrate a Fact stored in LangGraph state or AgentResult."""

        return cls(
            metric=str(value.get("metric", "unknown")),
            label=str(value.get("label", "")),
            value=value.get("value", ""),
            raw_value=value.get("raw_value", value.get("value", "")),
            unit=str(value.get("unit", "")),
            normalized_value=value.get("normalized_value"),
            period=str(value["period"]) if value.get("period") is not None else None,
            basis=str(value["basis"]) if value.get("basis") is not None else None,
            company=str(value["company"]) if value.get("company") is not None else None,
            document_id=str(value.get("document_id", "")),
            source=str(value.get("source", "")),
            evidence=str(value.get("evidence", "")),
            span_start=value.get("span_start"),
            span_end=value.get("span_end"),
            confidence=float(value.get("confidence", 0.0)),
            kind=str(value.get("kind", "numeric")),
            currency=str(value["currency"]) if value.get("currency") is not None else None,
            table_context=dict(value.get("table_context", {})),
        )

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
            "currency": self.currency,
            "table_context": dict(self.table_context),
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
    agent_results: list[dict[str, Any]] = field(default_factory=list)
    handoffs: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Stage3Result":
        """Rehydrate a result returned through the LangGraph state channel."""

        return cls(
            status=str(value.get("status", "error")),
            answer=str(value.get("answer", "")),
            facts=[dict(item) for item in value.get("facts", []) if isinstance(item, Mapping)],
            calculations=[dict(item) for item in value.get("calculations", []) if isinstance(item, Mapping)],
            comparison_results=[dict(item) for item in value.get("comparison_results", []) if isinstance(item, Mapping)],
            linked_events=[dict(item) for item in value.get("linked_events", []) if isinstance(item, Mapping)],
            citations=[dict(item) for item in value.get("citations", []) if isinstance(item, Mapping)],
            warnings=[str(item) for item in value.get("warnings", [])],
            provenance=[dict(item) for item in value.get("provenance", []) if isinstance(item, Mapping)],
            trace=[str(item) for item in value.get("trace", [])],
            agent_results=[dict(item) for item in value.get("agent_results", []) if isinstance(item, Mapping)],
            handoffs=[dict(item) for item in value.get("handoffs", []) if isinstance(item, Mapping)],
        )

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
            "agent_results": list(self.agent_results),
            "handoffs": list(self.handoffs),
        }


@dataclass(frozen=True)
class Stage3Intent:
    """Normalized contract consumed by the Stage3 pipeline."""

    question: str
    normalized_question: str
    route: Stage3Route
    intent: str
    question_type: str | None = None
    calculation: dict[str, Any] = field(default_factory=dict)
    metric: str | None = None
    basis: str | None = None
    time: dict[str, Any] = field(default_factory=dict)
    correction_mode: str | None = None
    manifest_filter: dict[str, Any] = field(default_factory=dict)
    companies: list[str] = field(default_factory=list)
    excluded_corps: list[dict[str, Any]] = field(default_factory=list)
    sector: str | None = None
    sector_members: list[str] = field(default_factory=list)
    ambiguous_mentions: list[Any] = field(default_factory=list)
    unknown_entities: list[Any] = field(default_factory=list)
    # Stage1 currently emits labels such as "high"/"low"; keep the value
    # unchanged so a future numeric confidence does not change the boundary.
    metric_confidence: Any = None
    allow_pdf_html: bool | None = None
    doc_count: int | None = None
    availability: str | None = None
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    reject_reason: str | None = None
    clarify_message: str | None = None
    llm_used: bool | None = None
    think_trace: str = ""
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
            "question_type": self.question_type,
            "calculation": dict(self.calculation),
            "metric": self.metric,
            "basis": self.basis,
            "time": dict(self.time),
            "correction_mode": self.correction_mode,
            "manifest_filter": dict(self.manifest_filter),
            "companies": list(self.companies),
            "excluded_corps": [dict(item) for item in self.excluded_corps],
            "sector": self.sector,
            "sector_members": list(self.sector_members),
            "ambiguous_mentions": list(self.ambiguous_mentions),
            "unknown_entities": list(self.unknown_entities),
            "metric_confidence": self.metric_confidence,
            "allow_pdf_html": self.allow_pdf_html,
            "doc_count": self.doc_count,
            "availability": self.availability,
            "assumptions": list(self.assumptions),
            "warnings": list(self.warnings),
            "missing_slots": list(self.missing_slots),
            "reject_reason": self.reject_reason,
            "clarify_message": self.clarify_message,
            "llm_used": self.llm_used,
            "think_trace": self.think_trace,
        }


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if str(item).strip()]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def _list_preserving_items(value: Any) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if item is not None]


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
    raw_calculation = source.get("calculation")
    calculation = dict(raw_calculation) if isinstance(raw_calculation, Mapping) else {}
    if not calculation and source.get("operation") is not None:
        calculation = {"operation": source.get("operation")}
    if source.get("denominator_metric") is not None and "denominator_metric" not in calculation:
        calculation["denominator_metric"] = source.get("denominator_metric")
    question_type = source.get("question_type", source.get("intent"))
    return Stage3Intent(
        question=raw_question,
        normalized_question=normalized,
        route=route,  # type: ignore[arg-type]
        intent=str(source.get("intent", "unsupported")),
        question_type=str(question_type).strip() if question_type is not None and str(question_type).strip() else None,
        calculation=calculation,
        metric=str(metric).strip() if metric is not None and str(metric).strip() else None,
        basis=str(basis).strip() if basis is not None and str(basis).strip() else None,
        time=_mapping(source.get("time")),
        correction_mode=str(source["correction_mode"]) if source.get("correction_mode") is not None else None,
        manifest_filter=_mapping(source.get("manifest_filter")),
        companies=companies,
        excluded_corps=[dict(item) for item in source.get("excluded_corps", []) if isinstance(item, Mapping)],
        sector=str(source["sector"]) if source.get("sector") is not None else None,
        sector_members=_list_of_strings(source.get("sector_members")),
        ambiguous_mentions=_list_preserving_items(source.get("ambiguous_mentions")),
        unknown_entities=_list_preserving_items(source.get("unknown_entities")),
        metric_confidence=source.get("metric_confidence"),
        allow_pdf_html=_optional_bool(source.get("allow_pdf_html")),
        doc_count=_optional_int(source.get("doc_count")),
        availability=str(source["availability"]) if source.get("availability") is not None else None,
        assumptions=_list_of_strings(source.get("assumptions")),
        warnings=_list_of_strings(source.get("warnings")),
        missing_slots=_list_of_strings(source.get("missing_slots")),
        reject_reason=str(source["reject_reason"]) if source.get("reject_reason") is not None else None,
        clarify_message=str(source["clarify_message"]) if source.get("clarify_message") is not None else None,
        llm_used=_optional_bool(source.get("llm_used")),
        think_trace=str(source.get("think_trace", "")),
        source=source,
    )
