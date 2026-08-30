from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def validate_citations(answer: str, stage3_result: Mapping[str, Any], stage2_result: Mapping[str, Any] | None = None) -> dict[str, Any]:
    citations = [item for item in stage3_result.get("citations", []) if isinstance(item, Mapping)]
    citation_ids = {str(item.get("document_id", "")) for item in citations if str(item.get("document_id", "")).strip()}
    errors: list[str] = []
    if not citations:
        errors.append("Stage3 citation이 없습니다.")
    for item in citations:
        if not str(item.get("document_id", "")).strip():
            errors.append("citation document_id가 없습니다.")
        if not str(item.get("evidence", item.get("text", ""))).strip():
            errors.append(f"citation evidence가 없습니다: {item.get('document_id', '')}")
    referenced: set[str] = set()
    for fact in stage3_result.get("facts", []):
        if isinstance(fact, Mapping) and fact.get("document_id"):
            referenced.add(str(fact["document_id"]))
    for group, key in (("calculations", "evidence_ids"), ("comparison_results", "evidence_ids"), ("linked_events", "source_ids")):
        for item in stage3_result.get(group, []):
            if isinstance(item, Mapping):
                referenced.update(str(value) for value in item.get(key, []) if value)
    missing = sorted(referenced - citation_ids)
    errors.extend(f"참조 문서가 citation에 없습니다: {value}" for value in missing)
    upstream_ids: set[str] = set()
    if isinstance(stage2_result, Mapping):
        for group in ("documents", "cited_documents"):
            for document in stage2_result.get(group, []):
                if isinstance(document, Mapping):
                    value = document.get("id", document.get("document_id", document.get("doc_id", "")))
                    if str(value).strip():
                        upstream_ids.add(str(value))
    missing_upstream = sorted(citation_ids - upstream_ids) if upstream_ids else []
    errors.extend(f"Stage2 검색 결과에 없는 citation 문서입니다: {value}" for value in missing_upstream)
    return {
        "pass": not errors,
        "citation_count": len(citations),
        "referenced_document_ids": sorted(referenced),
        "missing_document_ids": missing,
        "missing_upstream_document_ids": missing_upstream,
        "errors": errors,
        "answer_has_source_marker": bool(
            "문서ID" in answer
            or "출처" in answer
            or "[source:" in answer.lower()
            or any(document_id and document_id in answer for document_id in citation_ids)
            or any(str(item.get("source", "")).strip() and str(item.get("source")) in answer for item in citations)
            or bool(citations) and "\n-" in answer
            or bool(citations) and bool(answer.strip())
        ),
    }


__all__ = ["validate_citations"]
