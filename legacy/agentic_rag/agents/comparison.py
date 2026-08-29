from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance


def comparison_agent(state: dict[str, Any]) -> dict[str, Any]:
    documents = state.get("cited_documents", []) or state.get("retrieved_documents", [])
    grouped: dict[str, list[dict[str, Any]]] = {}
    for document in documents:
        metadata = document.get("metadata", {})
        company = str(metadata.get("corp_name", "미상"))
        grouped.setdefault(company, []).append(document)
    results = [{"company": company, "document_count": len(items), "sources": [item.get("source", "") for item in items]} for company, items in sorted(grouped.items())]
    ids = tuple(str(item.get("id", "")) for item in documents)
    result = AgentResult("comparison", "ok" if len(results) >= 2 else "insufficient", facts=tuple(results), evidence_ids=ids, confidence=1.0 if len(results) >= 2 else 0.4, trace=(f"companies={len(results)}",))
    provenance = Provenance("comparison", state.get("normalized_question", ""), ids, tuple(str(item.get("source", "")) for item in documents), result.confidence, {"companies": [item["company"] for item in results]})
    return {"comparison_results": results, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}

