from agentic_rag.contracts import HandoffRequest
from agentic_rag.registry import AgentRegistry


def create_handoff(registry: AgentRegistry, source: str, request: HandoffRequest) -> dict:
    registry.validate_target(source, request.target)
    if not request.task.strip() or not request.reason.strip():
        raise ValueError("Handoff task and reason are required")
    return {
        "source": source,
        "target": request.target,
        "task": request.task.strip(),
        "reason": request.reason.strip(),
        "evidence": list(request.evidence),
        "trace": list(request.trace),
    }

