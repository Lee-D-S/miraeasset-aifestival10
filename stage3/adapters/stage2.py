from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from stage3.contracts import Stage2Bundle, Stage3Document


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_spans(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_as_mapping(item) for item in value if isinstance(item, Mapping)]


def adapt_stage2_document(document: Mapping[str, Any]) -> Stage3Document:
    """Normalize one Stage2 document while preserving unknown metadata."""

    raw = dict(document)
    metadata = _as_mapping(raw.get("metadata"))
    # Some Stage2 candidates may expose metadata at the top level.  Preserve
    # it as a fallback without overriding an explicit nested value.
    for key in ("corp_name", "corp_code", "stock_code", "doc_group", "doc_subtype", "report_nm", "rcept_dt", "base_year", "base_month", "is_correction", "report_period", "basis"):
        if key not in metadata and key in raw:
            metadata[key] = raw[key]

    identifier = raw.get("id", raw.get("doc_id", raw.get("document_id", "")))
    source = raw.get("source", raw.get("source_path", raw.get("file_path", "")))
    text = raw.get("text", raw.get("content", raw.get("doc", "")))
    score = raw.get("score", raw.get("similarity", raw.get("rank_score")))
    try:
        normalized_score = float(score) if score is not None else None
    except (TypeError, ValueError):
        normalized_score = None

    return Stage3Document(
        id=str(identifier),
        source=str(source),
        text=str(text),
        score=normalized_score,
        metadata=metadata,
        evidence_spans=_as_spans(raw.get("evidence_spans", raw.get("citations", raw.get("spans", [])))),
        raw=raw,
    )


def _documents(value: Any) -> list[Stage3Document]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
        return []
    return [adapt_stage2_document(item) for item in value if isinstance(item, Mapping)]


def adapt_stage2_bundle(result: Any) -> Stage2Bundle:
    """Convert candidate Stage2 output shapes into ``Stage2Bundle``.

    Accepted shapes are a document list, a mapping with ``documents`` or
    ``retrieved_documents``, and a mapping with an optional
    ``cited_documents`` list.  The adapter intentionally does not decide how
    Stage2 retrieved or reranked the documents.
    """

    if isinstance(result, Mapping):
        raw = dict(result)
        documents = _documents(raw.get("documents", raw.get("retrieved_documents", raw.get("results", []))))
        cited = _documents(raw.get("cited_documents", raw.get("citedDocuments", [])))
        trace = raw.get("retrieval_trace", raw.get("trace", []))
        retrieval_trace = [str(item) for item in trace] if isinstance(trace, list) else ([str(trace)] if trace else [])
        return Stage2Bundle(documents=documents, cited_documents=cited, retrieval_trace=retrieval_trace, raw=raw)
    documents = _documents(result)
    return Stage2Bundle(documents=documents, raw={})
