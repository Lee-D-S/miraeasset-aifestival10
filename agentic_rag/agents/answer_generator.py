from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.evidence import context_text
from agentic_rag.llm.prompts import ANSWER_PROMPT
from agentic_rag.llm.history import HistoryPolicy


def make_answer_generator(client: Any | None = None):
    def generate(state: dict[str, Any]) -> dict[str, Any]:
        documents = state.get("cited_documents", [])
        if not documents:
            return {"status": "fallback", "fallback_reason": "답변을 뒷받침하는 근거 문서가 없습니다.", "trace": ["answer_skipped"]}
        history_mode = str(state.get("metadata", {}).get("history_mode", "minimal"))
        history = HistoryPolicy(mode="full" if history_mode == "full" else "minimal").select(state.get("messages", []))
        if client is None:
            answer = "\n".join(f"[출처: {item.get('source', '')}] {item.get('text', '')}" for item in documents[:3])
            mode = "template"
        else:
            answer = client.generate_answer(ANSWER_PROMPT.format(question=state.get("normalized_question", ""), context=context_text(documents)))
            mode = "llm"
        result = AgentResult("answer_generator", "ok" if answer.strip() else "empty", answer=answer, evidence_ids=tuple(str(item.get("id", "")) for item in documents), confidence=0.9 if answer.strip() else 0.0, trace=(f"mode={mode}",))
        provenance = Provenance("answer_generator", state.get("normalized_question", ""), result.evidence_ids, tuple(str(item.get("source", "")) for item in documents), result.confidence, {"mode": mode})
        return {"answer": answer, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "messages": [{"role": "assistant", "agent": "answer_generator", "content": answer, "history_count": len(history)}], "trace": list(result.trace)}
    return generate
