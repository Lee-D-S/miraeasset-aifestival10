from __future__ import annotations

from typing import Any

from agentic_rag.deterministic.metadata import extract_metadata, normalize_question


class AlternativeFinder:
    """Find deterministic reference documents for a fallback response.

    Alternative documents are deliberately kept separate from cited evidence.
    The finder only uses the retriever contract owned by the agentic backend.
    """

    def __init__(self, retriever: Any, *, limit: int = 3) -> None:
        self.retriever = retriever
        self.limit = max(1, int(limit))

    def find(self, question: str, metadata: dict[str, Any] | None = None) -> dict[str, list[dict[str, Any]]]:
        normalized = normalize_question(question)
        metadata = metadata or extract_metadata(normalized, self.retriever.corp_names())
        requested_company = str(metadata.get("corp_name", ""))
        requested_period = str(metadata.get("report_period", ""))
        document_type = str(metadata.get("document_type", ""))

        same_company: list[dict[str, Any]] = []
        same_period: list[dict[str, Any]] = []
        try:
            if requested_company:
                filters = {"corp_name": requested_company}
                if document_type:
                    filters["document_type"] = document_type
                candidates = self.retriever.search(normalized, limit=self.limit * 4, filters=filters)
                same_company = self._select(
                    candidates,
                    exclude_period=requested_period,
                    exclude_companies=(),
                )

            if requested_period:
                filters = {"report_period": requested_period}
                if document_type:
                    filters["document_type"] = document_type
                candidates = self.retriever.search(normalized, limit=self.limit * 4, filters=filters)
                same_period = self._select(
                    candidates,
                    exclude_period="",
                    exclude_companies=(requested_company,) if requested_company else (),
                )
            elif not requested_company:
                same_period = self._select(
                    self.retriever.search(normalized, limit=self.limit * 4, filters={}),
                    exclude_period="",
                    exclude_companies=(),
                )
        except Exception:
            return {"same_company": [], "same_period": []}

        return {"same_company": same_company, "same_period": same_period}

    def _select(
        self,
        documents: list[dict[str, Any]],
        *,
        exclude_period: str,
        exclude_companies: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        seen: set[str] = set()
        for document in sorted(documents, key=self._sort_key):
            metadata = document.get("metadata", {}) or {}
            if exclude_period and str(metadata.get("report_period", "")) == exclude_period:
                continue
            if str(metadata.get("corp_name", "")) in exclude_companies:
                continue
            document_id = str(document.get("id") or document.get("source") or document.get("text", ""))
            if not document_id or document_id in seen:
                continue
            seen.add(document_id)
            selected.append(document)
            if len(selected) >= self.limit:
                break
        return selected

    @staticmethod
    def _sort_key(document: dict[str, Any]) -> tuple[float, str, str, str]:
        metadata = document.get("metadata", {}) or {}
        return (
            -float(document.get("score", 0.0) or 0.0),
            str(metadata.get("report_period", "")),
            str(document.get("id", "")),
            str(document.get("source", "")),
        )
