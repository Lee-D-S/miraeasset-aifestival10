from __future__ import annotations

from typing import Any


REQUIRED_AGENT_RESULT_FIELDS = {
    "agent",
    "status",
    "confidence",
    "evidence_ids",
    "trace",
}


def validate_agent_result(payload: dict[str, Any]) -> tuple[bool, str]:
    missing = REQUIRED_AGENT_RESULT_FIELDS - set(payload)
    if missing:
        return False, f"Agent 결과 필드가 없습니다: {sorted(missing)}"
    confidence = payload.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= float(confidence) <= 1:
        return False, "Agent confidence는 0~1이어야 합니다."
    if not isinstance(payload.get("evidence_ids"), list):
        return False, "Agent evidence_ids는 배열이어야 합니다."
    if not isinstance(payload.get("trace"), list):
        return False, "Agent trace는 배열이어야 합니다."
    return True, "Agent schema가 유효합니다."


__all__ = ["REQUIRED_AGENT_RESULT_FIELDS", "validate_agent_result"]
