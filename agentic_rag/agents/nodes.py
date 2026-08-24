from __future__ import annotations

from typing import Any

from agentic_rag.deterministic.calculations import calculate
from agentic_rag.deterministic.metadata import detect_intent
from agentic_rag.deterministic.policy import policy_violation
from agentic_rag.contracts import HandoffRequest
from agentic_rag.handoff import create_handoff


def normalize_node(state: dict[str, Any]) -> dict[str, Any]:
    question = " ".join(state.get("question", "").replace("\u200b", " ").split())
    violation = policy_violation(question)
    return {"normalized_question": question, "fallback_reason": violation, "trace": ["normalized"]}


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    intent, confidence = detect_intent(state.get("normalized_question", ""), state.get("metadata", {}))
    return {"intent": intent, "intent_confidence": confidence, "trace": [f"intent={intent}:{confidence:.2f}"]}


def refine_intent_node(client: Any):
    def node(state: dict[str, Any]) -> dict[str, Any]:
        if client is None:
            return {"trace": ["intent_llm_skipped"]}
        try:
            parsed = client(state.get("normalized_question", "")) if callable(client) else client.generate_json(state.get("normalized_question", ""))
            allowed = {"lookup", "comparison", "calculation", "event_link", "fact_extraction", "unsupported"}
            intent = str(parsed.get("intent", state.get("intent", "fact_extraction")))
            if intent not in allowed:
                raise ValueError(f"Unsupported intent: {intent}")
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", state.get("intent_confidence", 0.0)))))
            metadata = parsed.get("metadata", {})
            return {"intent": intent, "intent_confidence": confidence, "metadata": metadata if isinstance(metadata, dict) else {}, "trace": ["intent_llm_used"]}
        except Exception:
            return {"trace": ["intent_llm_fallback"]}
    return node


def supervisor_node(registry: Any):
    mapping = {"lookup": "retrieval", "comparison": "comparison", "calculation": "calculation", "event_link": "event_linker", "fact_extraction": "fact_extractor"}

    def node(state: dict[str, Any]) -> dict[str, Any]:
        target = mapping.get(state.get("intent", "fact_extraction"), "fact_extractor")
        handoff = create_handoff(registry, "supervisor", HandoffRequest(target, state.get("normalized_question", ""), f"intent={state.get('intent')}"))
        return {"selected_agent": target, "handoffs": [handoff], "trace": [f"handoff=supervisor->{target}"]}
    return node


def retrieve_node(retriever: Any, limit: int):
    def node(state: dict[str, Any]) -> dict[str, Any]:
        documents = retriever.search(state.get("normalized_question", ""), limit, state.get("metadata", {}))
        return {"retrieved_documents": documents, "trace": [f"retrieved={len(documents)}"]}
    return node


def rerank_node(reranker: Any, limit: int):
    def node(state: dict[str, Any]) -> dict[str, Any]:
        docs = state.get("retrieved_documents", [])[:limit]
        cited = reranker.rerank(state.get("normalized_question", ""), docs) if hasattr(reranker, "rerank") else reranker(state.get("normalized_question", ""), docs)
        return {"cited_documents": cited, "trace": [f"cited={len(cited)}"]}
    return node


def specialist_node(state: dict[str, Any]) -> dict[str, Any]:
    docs = state.get("cited_documents", [])
    facts = [{"source": d.get("source", ""), "text": d.get("text", "")} for d in docs]
    return {"facts": facts, "calculations": calculate(state.get("calculations", {})), "trace": [f"specialist={state.get('selected_agent', 'unknown')}"]}


def answer_node(generator: Any):
    def node(state: dict[str, Any]) -> dict[str, Any]:
        if state.get("fallback_reason") or not state.get("cited_documents"):
            return {"answer": "", "status": "fallback", "trace": ["answer_skipped"]}
        answer = generator(state.get("normalized_question", ""), state.get("cited_documents", []), state.get("intent", "lookup"))
        return {"answer": answer, "trace": ["answer_generated"]}
    return node
