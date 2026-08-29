from __future__ import annotations

from typing import Any

from stage3.contracts import Stage3Intent


def route_after_gate(state: dict[str, Any]) -> str:
    intent = state.get("intent")
    return "blocked" if not intent or not intent.is_processable else "stage2_adapter"


def analysis_targets(intent: Stage3Intent) -> tuple[str, ...]:
    question_type = (intent.question_type or intent.intent or "").strip().lower()
    operation = str(intent.calculation.get("operation", "")).strip().lower()
    targets: list[str] = []
    if question_type in {"calc", "calculation"} and operation:
        targets.append("calculation_agent")
    if question_type in {"compare", "comparison"}:
        targets.append("comparison_agent")
    if question_type in {"event", "exists", "event_link", "change", "contract", "correction"} or intent.metric in {"supply_contract", "contract_termination"}:
        targets.append("event_linker_agent")
    return tuple(dict.fromkeys(targets))


def route_after_validation(state: dict[str, Any]) -> str:
    validation = state.get("validation", {})
    if validation.get("valid"):
        return "completed"
    return "fallback" if not state.get("fallback_used", False) else "failed"


__all__ = ["analysis_targets", "route_after_gate", "route_after_validation"]
