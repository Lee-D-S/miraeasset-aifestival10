from __future__ import annotations

from typing import Any

from agentic_rag.deterministic.calculations import calculate
from agentic_rag.deterministic.metadata import detect_intent
from agentic_rag.deterministic.policy import policy_violation
from agentic_rag.deterministic.evidence import validate_agent_outputs, validate_answer_claims
from agentic_rag.contracts import HandoffRequest
from agentic_rag.handoff import create_handoff


def normalize_node(state: dict[str, Any]) -> dict[str, Any]:
    question = " ".join(state.get("question", "").replace("\u200b", " ").split())
    violation = policy_violation(question)
    return {"normalized_question": question, "fallback_reason": violation, "messages": [{"role": "system", "agent": "normalizer", "content": question}], "trace": ["normalized"]}


def classify_node(state: dict[str, Any]) -> dict[str, Any]:
    intent, confidence = detect_intent(state.get("normalized_question", ""), state.get("metadata", {}))
    return {"intent": intent, "intent_confidence": confidence, "messages": [{"role": "system", "agent": "classifier", "content": intent}], "trace": [f"intent={intent}:{confidence:.2f}"]}


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
            return {"intent": intent, "intent_confidence": confidence, "metadata": metadata if isinstance(metadata, dict) else {}, "messages": [{"role": "assistant", "agent": "intent_parser", "content": str(parsed)}], "trace": ["intent_llm_used"]}
        except Exception:
            return {"trace": ["intent_llm_fallback"]}
    return node


def supervisor_node(registry: Any):
    mapping = {"lookup": "retrieval", "comparison": "comparison", "calculation": "calculation", "event_link": "event_linker", "fact_extraction": "fact_extractor"}

    def node(state: dict[str, Any]) -> dict[str, Any]:
        target = mapping.get(state.get("intent", "fact_extraction"), "fact_extractor")
        handoff = create_handoff(registry, "supervisor", HandoffRequest(target, state.get("normalized_question", ""), f"intent={state.get('intent')}"))
        return {"selected_agent": target, "handoffs": [handoff], "messages": [{"role": "assistant", "agent": "supervisor", "content": str(handoff)}], "trace": [f"handoff=supervisor->{target}"]}
    return node


def make_parallel_retrieve_node(retriever: Any, limit: int):
    def node(state: dict[str, Any]) -> dict[str, Any]:
        target = state.get("comparison_target", "")
        filters = {key: value for key, value in state.get("metadata", {}).items() if key in {"corp_name", "corp_code", "document_type", "report_period", "source_group"}}
        if target:
            filters["corp_name"] = target
        try:
            documents = retriever.search(state.get("normalized_question", ""), limit, filters)
            return {"parallel_documents": documents, "trace": [f"parallel_target={target}:documents={len(documents)}"]}
        except Exception as error:
            return {"parallel_documents": [], "parallel_failures": [{"target": target, "error": type(error).__name__}], "trace": [f"parallel_target={target}:failed"]}
    return node


def merge_parallel_node(state: dict[str, Any]) -> dict[str, Any]:
    unique: dict[str, dict[str, Any]] = {}
    for document in state.get("parallel_documents", []):
        unique[str(document.get("id", ""))] = document
    documents = [unique[key] for key in sorted(unique)]
    failures = state.get("parallel_failures", [])
    trace = [f"parallel_merged={len(documents)}"]
    if failures:
        trace.append(f"parallel_partial_failures={len(failures)}")
    return {"retrieved_documents": documents, "trace": trace}


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
        try:
            return generator(state)
        except TypeError:
            legacy_answer = generator(state.get("normalized_question", ""), state.get("cited_documents", []), state.get("intent", "lookup"))
            return {"answer": legacy_answer, "trace": ["answer_generated_legacy"]}
    return node


def validate_node(state: dict[str, Any]) -> dict[str, Any]:
    documents = state.get("cited_documents", [])
    provenance = state.get("provenance", [])
    valid, reason = validate_agent_outputs(documents, state.get("agent_results", []), provenance, state.get("retrieved_documents", []))
    if valid and not documents:
        valid, reason = False, "근거 문서가 없습니다."
    return {"validation": {"valid": valid, "reason": reason}, "fallback_reason": "" if valid else reason, "status": "validated" if valid else "fallback", "trace": ["validated" if valid else "validation_failed"]}


def final_validate_node(state: dict[str, Any]) -> dict[str, Any]:
    valid, reason = validate_answer_claims(state.get("answer", ""), state.get("cited_documents", []))
    return {"validation": {"valid": valid, "reason": reason}, "fallback_reason": "" if valid else reason, "status": "completed" if valid else "fallback", "trace": ["answer_validated" if valid else "answer_validation_failed"]}
