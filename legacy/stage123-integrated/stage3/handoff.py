from __future__ import annotations

from stage3.contracts import HandoffRequest
from stage3.registry import AgentRegistry


def create_handoff(registry: AgentRegistry, source: str, request: HandoffRequest) -> dict:
    registry.validate_target(source, request.target)
    if not request.task.strip() or not request.reason.strip():
        raise ValueError("Handoff task and reason are required")
    return request.to_dict()


__all__ = ["create_handoff"]
