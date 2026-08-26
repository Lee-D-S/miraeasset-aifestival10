from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class RetrievalError(RuntimeError):
    """Expected retrieval boundary failure with an explicit classification."""

    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class SearchRequest:
    query: str
    corp_names: tuple[str, ...] = ()
    sector: str | None = None
    doc_group: str | None = None
    doc_group_candidates: tuple[str, ...] = ()
    doc_subtype: str | None = None
    doc_subtype_candidates: tuple[str, ...] = ()
    base_years: tuple[int, ...] = ()
    base_months: tuple[int, ...] = ()
    start_date: str | None = None
    end_date: str | None = None
    section_name: str | None = None
    exclude_corp_name: str | None = None
    is_correction: bool | None = False
    basis: str | None = None
    report_nm_contains: tuple[str, ...] = ()
    top_k: int = 3
    sort_by_latest: bool = True
    limit: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "corp_names": list(self.corp_names),
            "sector": self.sector,
            "doc_group": self.doc_group,
            "doc_group_candidates": list(self.doc_group_candidates),
            "doc_subtype": self.doc_subtype,
            "doc_subtype_candidates": list(self.doc_subtype_candidates),
            "base_years": list(self.base_years),
            "base_months": list(self.base_months),
            "start_date": self.start_date,
            "end_date": self.end_date,
            "section_name": self.section_name,
            "exclude_corp_name": self.exclude_corp_name,
            "is_correction": self.is_correction,
            "basis": self.basis,
            "report_nm_contains": list(self.report_nm_contains),
            "top_k": self.top_k,
            "sort_by_latest": self.sort_by_latest,
            "limit": self.limit,
        }


@dataclass
class Stage2SearchResult:
    query_id: str
    search: SearchRequest
    documents: list[dict[str, Any]] = field(default_factory=list)
    cited_documents: list[dict[str, Any]] = field(default_factory=list)
    retrieval_trace: list[str] = field(default_factory=list)
    status: str = "ok"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "search": self.search.to_dict(),
            "documents": [dict(document) for document in self.documents],
            "cited_documents": [dict(document) for document in self.cited_documents],
            "retrieval_trace": list(self.retrieval_trace),
            "status": self.status,
            "warnings": list(self.warnings),
        }


class Stage2Repository(Protocol):
    def search(self, request: SearchRequest) -> Stage2SearchResult:
        ...
