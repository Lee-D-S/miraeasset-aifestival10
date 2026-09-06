from __future__ import annotations

from reasoner.contracts import HandoffRequest
from reasoner.registry import AgentRegistry


def create_handoff(registry: AgentRegistry, source: str, request: HandoffRequest) -> dict:
    registry.validate_target(source, request.target)
    if not request.task.strip() or not request.reason.strip():
        raise ValueError("Handoff task and reason are required")
    return request.to_dict()


__all__ = ["create_handoff"]
