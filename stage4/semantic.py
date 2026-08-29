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


def validate_semantics(client: Any, *, question: str, intent: Any, stage3_result: Mapping[str, Any], answer: str) -> dict[str, Any]:
    if client is None:
        raise RuntimeError("Stage4 semantic validator client is not configured")
    payload = {
        "question": question,
        "intent": intent,
        "facts": stage3_result.get("facts", []),
        "calculations": stage3_result.get("calculations", []),
        "comparison_results": stage3_result.get("comparison_results", []),
        "linked_events": stage3_result.get("linked_events", []),
        "citations": stage3_result.get("citations", []),
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
