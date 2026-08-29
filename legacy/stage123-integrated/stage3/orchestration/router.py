from __future__ import annotations

from typing import Any

from stage3.contracts import Stage3Intent


def route_after_gate(state: dict[str, Any]) -> str:
    intent = state.get("intent")
    return "blocked" if not intent or not intent.is_processable else "stage2_adapter"


def analysis_targets(intent: Stage3Intent) -> tuple[str, ...]:
    question = intent.normalized_question
    targets: list[str] = []
    if intent.intent in {"calc", "calculation"} or any(word in question for word in ("증감률", "증가율", "비중", "합계", "성장률", "CAGR", "영업이익률", "마진")):
        targets.append("calculation_agent")
    if intent.intent in {"compare", "comparison"} or any(word in question for word in ("비교", "어느 기업", "가장 큰", "순위")):
        targets.append("comparison_agent")
    if intent.intent in {"exists", "event_link", "change"} or any(word in question for word in ("계약", "해지", "정정", "후속")):
        targets.append("event_linker_agent")
    return tuple(dict.fromkeys(targets))


def route_after_validation(state: dict[str, Any]) -> str:
    validation = state.get("validation", {})
    if validation.get("valid"):
        return "completed"
    return "fallback" if not state.get("fallback_used", False) else "failed"


__all__ = ["analysis_targets", "route_after_gate", "route_after_validation"]
