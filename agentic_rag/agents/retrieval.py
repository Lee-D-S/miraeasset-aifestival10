from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance


def make_retrieval_agent(retriever: Any, limit: int):
    def agent(state: dict[str, Any]) -> dict[str, Any]:
        documents = retriever.search(state.get("normalized_question", ""), limit, state.get("metadata", {}))
        ids = tuple(str(item.get("id", "")) for item in documents)
        sources = tuple(str(item.get("source", "")) for item in documents)
        result = AgentResult("retrieval", "ok" if documents else "empty", evidence_ids=ids, confidence=max((float(item.get("score", 0)) for item in documents), default=0.0), trace=(f"retrieved={len(documents)}",))
        provenance = Provenance("retrieval", state.get("normalized_question", ""), ids, sources, result.confidence, {"filters": state.get("metadata", {})})
        return {"retrieved_documents": documents, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}
    return agent

