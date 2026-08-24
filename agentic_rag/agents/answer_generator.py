from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.evidence import context_text
from agentic_rag.llm.prompts import ANSWER_PROMPT
from agentic_rag.llm.history import HistoryPolicy


def make_answer_generator(client: Any | None = None, *, rag_reasoning: Any | None = None, search_tool: Any | None = None):
    def generate(state: dict[str, Any]) -> dict[str, Any]:
        documents = state.get("cited_documents", [])
        if not documents:
            return {"status": "fallback", "fallback_reason": "답변을 뒷받침하는 근거 문서가 없습니다.", "trace": ["answer_skipped"]}
        history_mode = str(state.get("metadata", {}).get("history_mode", "minimal"))
        history = HistoryPolicy(mode="full" if history_mode == "full" else "minimal").select(state.get("messages", []))
        rag_details: dict[str, Any] = {}
        intent = state.get("intent", "lookup")
        use_rag_reasoning = bool(rag_reasoning and search_tool and intent in {"comparison", "event_link", "fact_extraction"} and len(documents) >= 2)
        if use_rag_reasoning:
            try:
                rag_result = rag_reasoning.generate_grounded_answer(state.get("normalized_question", ""), documents)
                answer = rag_result["answer"]
                rag_details = {"tool_calls": rag_result.get("tool_calls", []), "tool_document_ids": [str(item.get("id", "")) for item in rag_result.get("documents", [])], "usage": rag_result.get("usage", {})}
                mode = "rag_reasoning_tool"
            except Exception as exc:
                state.setdefault("trace", []).append(f"rag_reasoning_fallback={type(exc).__name__}")
                use_rag_reasoning = False
        if not use_rag_reasoning and client is None:
            answer = "\n".join(f"[출처: {item.get('source', '')}] {item.get('text', '')}" for item in documents[:3])
            mode = "template"
        elif not use_rag_reasoning:
            answer = client.generate_text([{"role": "user", "content": ANSWER_PROMPT.format(question=state.get("normalized_question", ""), context=context_text(documents))}])
            mode = "llm"
        result = AgentResult("answer_generator", "ok" if answer.strip() else "empty", answer=answer, evidence_ids=tuple(str(item.get("id", "")) for item in documents), confidence=0.9 if answer.strip() else 0.0, trace=(f"mode={mode}",))
        provenance = Provenance("answer_generator", state.get("normalized_question", ""), result.evidence_ids, tuple(str(item.get("source", "")) for item in documents), result.confidence, {"mode": mode, "tool_call": mode == "rag_reasoning_tool", **rag_details})
        return {"answer": answer, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "messages": [{"role": "assistant", "agent": "answer_generator", "content": answer, "history_count": len(history)}], "trace": list(result.trace)}
    return generate
