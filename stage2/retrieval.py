"""Dependency-free Stage2 retrieval core.

The production SQLite/Chroma adapters can be added later without changing the
Stage2 node contract.  This module deliberately does not call an LLM or load
an embedding model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from integration.rate_limit import is_rate_limit_error, rate_limit_event


_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_SPACED_HANGUL_RE = re.compile(r"(?<![가-힣])(?:[가-힣]\s+){2,}[가-힣](?![가-힣])")
_KOREAN_PARTICLES = ("으로", "에서", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "과", "와", "도", "로")


def _text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _tokens(value: Any) -> set[str]:
    raw = _text(value)
    compact = _SPACED_HANGUL_RE.sub(lambda match: re.sub(r"\s+", "", match.group(0)), raw)
    tokens = {token.lower() for token in _TOKEN_RE.findall(raw + " " + compact)}
    for token in tuple(tokens):
        for particle in _KOREAN_PARTICLES:
            if token.endswith(particle) and len(token) > len(particle) + 1:
                tokens.add(token[: -len(particle)])
                break
    return tokens


def _metadata(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("metadata")
    return value if isinstance(value, Mapping) else document


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _normalized_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no"}:
            return False
    return bool(value)


def _date_value(value: Any) -> str:
    return _text(value).replace("-", "")


def matches_manifest_filter(document: Mapping[str, Any], manifest_filter: Mapping[str, Any]) -> bool:
    """Return whether one document satisfies Stage1's manifest filter."""

    metadata = _metadata(document)
    corp_names = {_text(item) for item in _as_list(manifest_filter.get("corp_names")) if _text(item)}
    corp_name = _text(metadata.get("corp_name"))
    excluded_corp_names = {
        _text(item) for item in _as_list(manifest_filter.get("exclude_corp_names")) if _text(item)
    }
    if excluded_corp_names and corp_name in excluded_corp_names:
        return False
    if corp_names and corp_name not in corp_names:
        return False

    sector = _text(manifest_filter.get("sector"))
    if sector and sector not in _text(metadata.get("sector")):
        return False

    doc_group = _text(manifest_filter.get("doc_group"))
    doc_groups = {_text(item) for item in _as_list(manifest_filter.get("doc_group_candidates"))}
    if doc_group and _text(metadata.get("doc_group")) != doc_group:
        return False
    if not doc_group and doc_groups and _text(metadata.get("doc_group")) not in doc_groups:
        return False

    doc_subtype = _text(manifest_filter.get("doc_subtype"))
    doc_subtypes = {_text(item) for item in _as_list(manifest_filter.get("doc_subtype_candidates"))}
    actual_subtype = _text(metadata.get("doc_subtype"))
    if doc_subtype and actual_subtype != doc_subtype:
        return False
    if not doc_subtype and doc_subtypes and actual_subtype not in doc_subtypes:
        return False

    years = {str(item) for item in _as_list(manifest_filter.get("base_years"))}
    months = {str(item) for item in _as_list(manifest_filter.get("base_months"))}
    if years and str(metadata.get("base_year")) not in years:
        return False
    if months and str(metadata.get("base_month")) not in months:
        return False

    received = _date_value(metadata.get("rcept_dt"))
    start = _date_value(manifest_filter.get("rcept_from"))
    end = _date_value(manifest_filter.get("rcept_to"))
    if start and (not received or received < start):
        return False
    if end and (not received or received > end):
        return False

    correction = _normalized_bool(manifest_filter.get("is_correction"))
    if correction is not None and _normalized_bool(metadata.get("is_correction")) != correction:
        return False

    report_terms = [_text(item).lower() for item in _as_list(manifest_filter.get("report_nm_contains"))]
    report_name = _text(metadata.get("report_nm")).lower()
    if report_terms and not any(term in report_name for term in report_terms):
        return False
    return True


def build_search_query(question: str, intent: Mapping[str, Any]) -> str:
    """Build a deterministic search query without reinterpreting the question."""

    normalized = _text(intent.get("normalized_question")) or _text(question)
    metric = _text(intent.get("metric"))
    basis = _text(intent.get("basis"))
    parts = [normalized]
    if metric and metric not in normalized:
        parts.append(metric)
    if basis and basis not in normalized:
        parts.append(basis)
    return " ".join(part for part in parts if part)


class Stage2Retriever(Protocol):
    """Backend contract for metadata, keyword, and vector retrieval."""

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[Mapping[str, Any]]:
        ...

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...


class Reranker(Protocol):
    """Rank merged candidates independently from keyword/vector retrieval."""

    def rerank(self, query: str, documents: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...


class DeterministicReranker:
    """Explicit test/local reranker; production can inject CLOVA here."""

    def rerank(self, query: str, documents: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        del query
        return sorted(
            (dict(document) for document in documents),
            key=lambda document: (-float(document.get("hybrid_score", document.get("score", 0.0))), _document_id(document)),
        )[:limit]


@dataclass(frozen=True)
class RetrievalConfig:
    candidate_limit: int = 50
    branch_limit: int = 20
    final_limit: int = 8
    keyword_weight: float = 0.5
    vector_weight: float = 0.5
    reranker: Reranker | None = None


class InMemoryRetriever:
    """Small deterministic backend for tests and local contract development."""

    def __init__(self, documents: Sequence[Mapping[str, Any]], vector_scores: Mapping[str, float] | None = None):
        self.documents = [dict(document) for document in documents]
        self.vector_scores = dict(vector_scores or {})

    @staticmethod
    def _id(document: Mapping[str, Any]) -> str:
        return _text(document.get("id") or document.get("chunk_id") or document.get("doc_id"))

    @staticmethod
    def _content(document: Mapping[str, Any]) -> str:
        return _text(document.get("text") or document.get("page_content") or document.get("text_content"))

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[Mapping[str, Any]]:
        matched = [document for document in self.documents if matches_manifest_filter(document, manifest_filter)]
        return matched[:limit]

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        query_tokens = _tokens(query)
        scored = []
        for document in candidates:
            content_tokens = _tokens(self._content(document))
            score = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            if score > 0:
                scored.append((score, self._id(document), document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(document), "keyword_score": score} for score, _, document in scored[:limit]]

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        del query
        scored = []
        for document in candidates:
            identifier = self._id(document)
            score = float(self.vector_scores.get(identifier, document.get("vector_score", 0.0)))
            if score > 0:
                scored.append((score, identifier, document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(document), "vector_score": score} for score, _, document in scored[:limit]]


def _document_id(document: Mapping[str, Any]) -> str:
    return _text(document.get("id") or document.get("chunk_id") or document.get("doc_id") or document.get("document_id"))


def _normalize_document(document: Mapping[str, Any], score: float, sources: list[str]) -> dict[str, Any]:
    raw = dict(document)
    metadata = dict(raw.get("metadata")) if isinstance(raw.get("metadata"), Mapping) else {}
    for key, value in raw.items():
        if key not in metadata and key not in {"text", "page_content", "text_content", "metadata"}:
            metadata[key] = value
    identifier = _document_id(raw)
    text = _text(raw.get("text") or raw.get("page_content") or raw.get("text_content"))
    return {
        "id": identifier,
        "doc_id": _text(raw.get("doc_id") or metadata.get("doc_id") or identifier),
        "chunk_id": _text(raw.get("chunk_id") or metadata.get("chunk_id") or identifier),
        "source": _text(raw.get("source") or raw.get("file_path") or metadata.get("source") or metadata.get("file_path")),
        "text": text,
        "score": round(score, 6),
        "metadata": metadata,
        "evidence_spans": list(raw.get("evidence_spans") or []),
        "match_sources": sources,
    }


def retrieve(
    *,
    question_id: str,
    question: str,
    intent: Mapping[str, Any],
    route: str,
    retriever: Stage2Retriever,
    config: RetrievalConfig = RetrievalConfig(),
    search_query: str | None = None,
) -> dict[str, Any]:
    """Run Stage2 and return the canonical shared-state envelope."""

    empty = {
        "query_id": _text(question_id),
        "documents": [],
        "cited_documents": [],
        "retrieval_trace": [],
        "warnings": [],
        "provider_status": {},
    }
    if route != "ok":
        return {**empty, "status": "skipped", "retrieval_trace": [f"route={route}"]}

    manifest_filter = intent.get("manifest_filter")
    if not isinstance(manifest_filter, Mapping):
        return {**empty, "status": "error", "warnings": ["Stage1 manifest_filter가 없습니다."]}

    query = _text(search_query) or build_search_query(question, intent)
    candidates = retriever.filter_candidates(manifest_filter, config.candidate_limit)
    keyword_results = retriever.keyword_search(query, candidates, config.branch_limit)
    try:
        vector_results = retriever.vector_search(query, candidates, config.branch_limit)
    except Exception as error:  # provider/backend boundary; never fake semantic success
        provider_status = {}
        if is_rate_limit_error(error):
            provider_status = rate_limit_event(
                error,
                operation="embedding",
                client=getattr(retriever, "query_embedder", None),
            )
            classification = "rate_limited"
        else:
            classification = getattr(error, "classification", "embedding_unavailable")
        return {
            **empty,
            "status": str(classification),
            "retrieval_trace": [f"query={query}", f"candidate_count={len(candidates)}"],
            "warnings": [str(error)],
            "provider_status": provider_status,
        }

    merged: dict[str, dict[str, Any]] = {}
    for branch, results in (("keyword", keyword_results), ("vector", vector_results)):
        for rank, result in enumerate(results):
            identifier = _document_id(result)
            if not identifier:
                continue
            item = merged.setdefault(identifier, {"raw": dict(result), "sources": set(), "keyword": 0.0, "vector": 0.0})
            item["sources"].add(branch)
            if branch == "keyword":
                item["keyword"] = max(float(item["keyword"]), float(result.get("keyword_score", 0.0)))
            else:
                item["vector"] = max(float(item["vector"]), float(result.get("vector_score", 0.0)))
            item["raw"].setdefault("_rank_" + branch, rank)

    merged_documents = []
    for item in merged.values():
        hybrid_score = config.keyword_weight * item["keyword"] + config.vector_weight * item["vector"]
        document = _normalize_document(item["raw"], hybrid_score, sorted(item["sources"]))
        document["hybrid_score"] = hybrid_score
        merged_documents.append(document)
    reranker = config.reranker or DeterministicReranker()
    documents = reranker.rerank(query, merged_documents, config.final_limit)
    cited = documents
    status = "ok" if cited else "not_found"
    return {
        "query_id": _text(question_id),
        "status": status,
        "documents": documents,
        "cited_documents": cited,
        "retrieval_trace": [
            f"query={query}",
            f"candidate_count={len(candidates)}",
            f"keyword_count={len(keyword_results)}",
            f"vector_count={len(vector_results)}",
            f"merged_count={len(documents)}",
            f"cited_count={len(cited)}",
        ],
        "warnings": [],
    }


__all__ = [
    "InMemoryRetriever",
    "RetrievalConfig",
    "Stage2Retriever",
    "build_search_query",
    "matches_manifest_filter",
    "retrieve",
]
