from __future__ import annotations

import json
from typing import Any, Mapping


SEMANTIC_SCHEMA = {
    "type": "object",
    "required": ["pass", "issues", "unsupported_claims", "missing_aspects", "summary"],
    "properties": {
        "pass": {"type": "boolean"},
        "issues": {"type": "array"},
        "unsupported_claims": {"type": "array"},
        "missing_aspects": {"type": "array"},
        "summary": {"type": "string"},
    },
}


def _compact_stage3_result(stage3_result: Mapping[str, Any]) -> dict[str, Any]:
    """Keep semantic validation within the model context window."""
    facts = []
    for fact in stage3_result.get("facts", [])[:20]:
        if isinstance(fact, Mapping):
            facts.append({key: fact.get(key) for key in (
                "metric", "label", "value", "unit", "normalized_value", "period",
                "basis", "company", "document_id",
            )})
    citations = []
    for citation in stage3_result.get("citations", [])[:8]:
        if isinstance(citation, Mapping):
            citations.append({
                "document_id": citation.get("document_id"),
                "source": citation.get("source"),
                "evidence": str(citation.get("evidence", ""))[:800],
            })
    return {
        "facts": facts,
        "calculations": list(stage3_result.get("calculations", []))[:20],
        "comparison_results": list(stage3_result.get("comparison_results", []))[:10],
        "linked_events": list(stage3_result.get("linked_events", []))[:10],
        "citations": citations,
    }


def validate_semantics(client: Any, *, question: str, intent: Any, stage3_result: Mapping[str, Any], answer: str) -> dict[str, Any]:
    if client is None:
        raise RuntimeError("Stage4 semantic validator client is not configured")
    payload = {
        "question": question,
        "intent": intent,
        **_compact_stage3_result(stage3_result),
        "answer": answer,
    }
    result = client.generate_json([{
        "role": "user",
        "content": "질문과 공시 근거에 비추어 답변의 의미가 정확한지 판정하세요. 근거 밖 주장은 실패입니다. "
        "지정된 JSON schema의 객체 하나만 반환하세요.\n자료:\n" + json.dumps(payload, ensure_ascii=False),
    }], schema=SEMANTIC_SCHEMA)
    if not isinstance(result, Mapping):
        raise ValueError("semantic validator returned a non-object")
    normalized = dict(result)
    for key in ("issues", "unsupported_claims", "missing_aspects"):
        if not isinstance(normalized.get(key), list):
            raise ValueError(f"semantic validator field {key} must be an array")
    if not isinstance(normalized.get("pass"), bool) or not isinstance(normalized.get("summary"), str):
        raise ValueError("semantic validator returned an invalid schema")
    return normalized


__all__ = ["SEMANTIC_SCHEMA", "validate_semantics"]
