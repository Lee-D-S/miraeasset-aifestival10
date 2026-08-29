from __future__ import annotations

from typing import Any

from stage3.contracts import Stage3Intent


SUPPORTED_OPERATIONS = frozenset({
    "add",
    "subtract",
    "multiply",
    "divide",
    "percentage_change",
    "cagr",
    "ratio_percent",
    "margin",
    "sum",
    "average",
    "min",
    "max",
    "rank",
})


def build_calculation_plan(intent: Stage3Intent) -> dict[str, Any] | None:
    """Read the operation selected by Stage1 without reading question text."""

    question_type = (intent.question_type or intent.intent or "").strip().lower()
    if question_type in {"compare", "comparison"}:
        calculation = dict(intent.calculation)
        calculation.setdefault("operation", "rank")
        calculation.setdefault("metric", intent.metric)
        return calculation
    calculation = dict(intent.calculation)
    operation = calculation.get("operation")
    if not operation:
        return None
    calculation.setdefault("metric", intent.metric)
    return calculation


__all__ = ["SUPPORTED_OPERATIONS", "build_calculation_plan"]
