from __future__ import annotations

from typing import Any

from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.evidence import context_text
from agentic_rag.llm.prompts import FACT_EXTRACTION_PROMPT
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE
from agentic_rag.llm.schemas import FACT_EXTRACTION_SCHEMA


def fact_extractor_agent(state: dict[str, Any]) -> dict[str, Any]:
    facts = []
    documents = state.get("cited_documents", []) or state.get("retrieved_documents", [])
    for document in documents:
        text = str(document.get("text", "")).strip()
        if text:
            facts.append({"document_id": str(document.get("id", "")), "source": str(document.get("source", "")), "fact": text})
    ids = tuple(str(item["document_id"]) for item in facts)
    result = AgentResult("fact_extractor", "ok" if facts else "empty", facts=tuple(facts), evidence_ids=ids, confidence=0.8 if facts else 0.0, trace=(f"facts={len(facts)}",))
    provenance = Provenance("fact_extractor", state.get("normalized_question", ""), ids, tuple(str(item["source"]) for item in facts), result.confidence)
    return {"facts": facts, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}


def make_fact_extractor_agent(client: Any | None = None):
    def agent(state: dict[str, Any]) -> dict[str, Any]:
        deterministic = fact_extractor_agent(state)
        if client is None or not state.get("cited_documents") or deterministic.get("facts"):
            return deterministic
        try:
            parsed = client.generate_json([{"role": "user", "content": FACT_EXTRACTION_PROMPT.format(question=state.get("normalized_question", ""), context=context_text(state.get("cited_documents", [])))}], schema=FACT_EXTRACTION_SCHEMA, profile=DEFAULT_PROFILE)
            facts = parsed.get("facts", []) if isinstance(parsed, dict) else []
            allowed = {str(item.get("id", "")): item for item in state.get("cited_documents", [])}
            facts = [item for item in facts if str(item.get("document_id", "")) in allowed]
            return {**deterministic, "facts": facts, "trace": ["fact_extractor_llm_used"]}
        except Exception:
            return deterministic
    return agent
