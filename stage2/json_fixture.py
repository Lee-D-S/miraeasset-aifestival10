"""Adapter for the legacy embedded disclosure JSON fixture.

This is intentionally a Stage2 backend adapter, not a second pipeline.  It
normalizes legacy metadata and requires an injected query embedder for vector
search; no incompatible fake embedding is ever generated here.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from stage2.retrieval import matches_manifest_filter


class EmbeddingUnavailable(RuntimeError):
    """Raised when semantic retrieval has no query embedding provider."""


def _period(value: Any) -> tuple[int | None, int | None]:
    match = re.match(r"^(20\d{2})-(\d{1,2})$", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _subtype(document_type: Any, month: int | None) -> str | None:
    value = str(document_type or "")
    if "사업보고서" in value:
        return "annual"
    if "반기보고서" in value:
        return "half"
    if "분기보고서" in value:
        return "quarter"
    return {3: "quarter", 6: "half", 9: "quarter", 12: "annual"}.get(month)


def normalize_fixture_row(row: Mapping[str, Any]) -> dict[str, Any]:
    raw_metadata = row.get("metadata")
    source = dict(raw_metadata) if isinstance(raw_metadata, Mapping) else {}
    year, month = _period(source.get("report_period"))
    document_type = source.get("document_type", "")
    source.update(
        {
            "doc_group": source.get("doc_group", source.get("source_group")),
            "doc_subtype": source.get("doc_subtype", _subtype(document_type, month)),
            "base_year": source.get("base_year", year),
            "base_month": source.get("base_month", month),
            "rcept_dt": str(source.get("rcept_dt", source.get("disclosure_date", ""))).replace("-", ""),
            "report_nm": source.get("report_nm", document_type),
            "is_correction": bool(source.get("is_correction", False)),
            "chunk_id": source.get("chunk_id", row.get("id")),
            "source": source.get("source", row.get("source_path", "")),
        }
    )
    return {
        "id": str(row.get("id") or source.get("chunk_id") or ""),
        "text": str(row.get("text") or ""),
        "source": str(row.get("source_path") or source.get("source") or ""),
        "metadata": source,
        "embedding": row.get("embedding"),
    }


class JsonFixtureRetriever:
    """Hybrid-compatible retriever over ``disclosure_clova_local.json``."""

    def __init__(self, documents: Sequence[Mapping[str, Any]], *, query_embedder: Callable[[str], Iterable[float]] | None = None):
        self.documents = [normalize_fixture_row(row) for row in documents]
        self.query_embedder = query_embedder

    @classmethod
    def from_path(cls, path: str | Path, *, query_embedder: Callable[[str], Iterable[float]] | None = None) -> "JsonFixtureRetriever":
        source = Path(path).expanduser().resolve()
        raw = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(raw, list) or not all(isinstance(row, Mapping) for row in raw):
            raise ValueError("fixture must contain a list of objects")
        return cls(raw, query_embedder=query_embedder)

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            raise ValueError(f"embedding dimension mismatch: stored={len(left)}, query={len(right)}")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[Mapping[str, Any]]:
        return [row for row in self.documents if matches_manifest_filter(row, manifest_filter)][:limit]

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        query_tokens = set(re.findall(r"\w+", query.lower()))
        ranked = []
        for row in candidates:
            score = len(query_tokens & set(re.findall(r"\w+", str(row.get("text", "")).lower()))) / max(len(query_tokens), 1)
            if score > 0:
                ranked.append((score, str(row.get("id", "")), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(row), "keyword_score": score} for score, _, row in ranked[:limit]]

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        if self.query_embedder is None:
            raise EmbeddingUnavailable("query embedding provider is not configured")
        vector = [float(value) for value in self.query_embedder(query)]
        if len(vector) != 1024:
            raise ValueError(f"query embedding must have 1024 dimensions, got {len(vector)}")
        ranked = []
        for row in candidates:
            stored = row.get("embedding")
            if not isinstance(stored, list) or len(stored) != 1024:
                raise ValueError(f"document {row.get('id', '')} embedding must have 1024 dimensions")
            score = self._cosine([float(value) for value in stored], vector)
            if score > 0:
                ranked.append((score, str(row.get("id", "")), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(row), "vector_score": score} for score, _, row in ranked[:limit]]


__all__ = ["EmbeddingUnavailable", "JsonFixtureRetriever", "normalize_fixture_row"]
