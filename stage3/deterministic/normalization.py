from __future__ import annotations

from dataclasses import replace
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent


UNIT_MULTIPLIERS: dict[str, float] = {
    "조원": 1_000_000_000_000,
    "조": 1_000_000_000_000,
    "십억원": 1_000_000_000,
    "억원": 100_000_000,
    "백만원": 1_000_000,
    "천만원": 10_000_000,
    "만원": 10_000,
    "천원": 1_000,
    "원": 1,
    "%": 1,
    "": 1,
}


def normalize_number(value: float | str, unit: str) -> float | str | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return value
    multiplier = UNIT_MULTIPLIERS.get(unit)
    if multiplier is None:
        return None
    return float(value) * multiplier


def _period_from_intent(intent: Stage3Intent) -> str | None:
    time: dict[str, Any] = intent.time
    years = time.get("years") or []
    months = time.get("base_months") or []
    if len(years) == 1:
        year = str(years[0])
        return f"{year}-{int(months[0]):02d}" if len(months) == 1 else year
    return None


def normalize_facts(facts: list[Stage3Fact], intent: Stage3Intent) -> tuple[list[Stage3Fact], list[str]]:
    """Apply Stage1 defaults and reject no values; retain warnings for review."""

    warnings: list[str] = []
    default_period = _period_from_intent(intent)
    normalized: list[Stage3Fact] = []
    for fact in facts:
        value = fact.normalized_value
        if value is None and isinstance(fact.value, (int, float)):
            value = normalize_number(float(fact.value), fact.unit)
        if value is None and fact.kind == "numeric":
            warnings.append(f"{fact.document_id}: 알 수 없는 단위 '{fact.unit}'")
        period = fact.period or default_period
        basis = fact.basis or intent.basis
        if fact.kind == "numeric" and not period:
            warnings.append(f"{fact.document_id}: {fact.label}의 기준 기간을 확인할 수 없습니다.")
        if fact.kind == "numeric" and not basis:
            warnings.append(f"{fact.document_id}: {fact.label}의 연결/별도 기준을 확인할 수 없습니다.")
        normalized.append(replace(fact, normalized_value=value, period=period, basis=basis))
    return normalized, warnings
