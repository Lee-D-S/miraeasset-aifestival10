from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.evidence import context_text
from agentic_rag.llm.prompts import EVENT_LINK_PROMPT
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE
from agentic_rag.llm.schemas import EVENT_LINK_SCHEMA


def event_linker_agent(state: dict[str, Any]) -> dict[str, Any]:
    keywords = [word for word in ("합병", "분할", "계약", "소송", "발행", "취득", "공시") if word in state.get("normalized_question", "")]
    linked = []
    for document in state.get("cited_documents", []) or state.get("retrieved_documents", []):
        text = str(document.get("text", ""))
        matches = [keyword for keyword in keywords if keyword in text]
        if matches:
            linked.append({"event": matches[0], "document_id": str(document.get("id", "")), "source": document.get("source", ""), "confidence": min(1.0, 0.5 + 0.1 * len(matches))})
    ids = tuple(str(item.get("document_id", "")) for item in linked)
    confidence = max((float(item.get("confidence", 0)) for item in linked), default=0.0)
    result = AgentResult("event_linker", "ok" if linked else "empty", facts=tuple(linked), evidence_ids=ids, confidence=confidence, trace=(f"linked_events={len(linked)}",))
    provenance = Provenance("event_linker", state.get("normalized_question", ""), ids, tuple(str(item.get("source", "")) for item in linked), confidence, {"keywords": keywords})
    return {"linked_events": linked, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}


def make_event_linker_agent(client: Any | None = None):
    def agent(state: dict[str, Any]) -> dict[str, Any]:
        deterministic = event_linker_agent(state)
        if client is None or not state.get("cited_documents") or deterministic.get("linked_events"):
            return deterministic
        try:
            parsed = client.generate_json([{"role": "user", "content": EVENT_LINK_PROMPT.format(question=state.get("normalized_question", ""), context=context_text(state.get("cited_documents", [])))}], schema=EVENT_LINK_SCHEMA, profile=DEFAULT_PROFILE)
            linked = parsed.get("events", []) if isinstance(parsed, dict) else []
            allowed = {str(item.get("id", "")): item for item in state.get("cited_documents", [])}
            linked = [item for item in linked if str(item.get("document_id", "")) in allowed]
            return {**deterministic, "linked_events": linked, "trace": ["event_linker_llm_used"]}
        except Exception:
            return deterministic
    return agent
