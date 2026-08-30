from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


_NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?")
_UNIT_MULTIPLIERS = {
    "조": Decimal("1000000000000"),
    "십억": Decimal("1000000000"),
    "억": Decimal("100000000"),
    "천만": Decimal("10000000"),
    "백만": Decimal("1000000"),
    "만": Decimal("10000"),
    "천": Decimal("1000"),
    "백": Decimal("100"),
    "십": Decimal("10"),
}


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None


def _unit_multiplier(text: str, end: int) -> tuple[Decimal, str]:
    suffix = text[end: end + 4]
    for unit in sorted(_UNIT_MULTIPLIERS, key=len, reverse=True):
        if suffix.startswith(unit):
            return _UNIT_MULTIPLIERS[unit], unit
    return Decimal("1"), ""


def extract_answer_numbers(answer: str) -> list[dict[str, Any]]:
    """Extract answer numbers while ignoring dates, ranks, and document IDs."""

    values: list[dict[str, Any]] = []
    for match in _NUMBER.finditer(answer):
        start, end = match.span()
        before = answer[max(0, start - 12):start]
        after = answer[end:end + 12]
        if re.search(r"문서\s*ID|Q[-_]?$|질의\s*ID|\[?source:\s*", before, re.IGNORECASE):
            continue
        if re.match(r"\s*(년|월|일|위|분기|분기말)", after):
            continue
        raw = match.group(0)
        base = _decimal(raw)
        if base is None:
            continue
        multiplier, unit = _unit_multiplier(answer, end)
        percent = bool(re.match(r"\s*(%|퍼센트)", after))
        values.append({
            "raw": raw,
            "value": base * multiplier,
            "unit": unit + ("%" if percent else ""),
            "percent": percent,
            "start": start,
            "end": end,
        })
    return values


def _candidate_values(stage3_result: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for fact in stage3_result.get("facts", []):
        if not isinstance(fact, Mapping):
            continue
        normalized = fact.get("normalized_value")
        value = normalized if normalized is not None else fact.get("value")
        number = _decimal(value)
        if number is None:
            continue
        unit = str(fact.get("unit", ""))
        multiplier = Decimal("1") if normalized is not None else next((m for u, m in sorted(_UNIT_MULTIPLIERS.items(), key=lambda x: len(x[0]), reverse=True) if u in unit), Decimal("1"))
        candidates.append({"value": number * multiplier, "id": fact.get("document_id", ""), "kind": "fact"})
    for group in ("calculations", "comparison_results"):
        for item in stage3_result.get(group, []):
            if not isinstance(item, Mapping):
                continue
            for key in ("result", "value"):
                number = _decimal(item.get(key))
                if number is not None:
                    candidates.append({"value": number, "id": item.get("operation", group), "kind": group})
            for result in item.get("results", []):
                if isinstance(result, Mapping):
                    number = _decimal(result.get("value"))
                    if number is not None:
                        candidates.append({"value": number, "id": result.get("company", group), "kind": group})
    return candidates


def validate_numeric_answer(answer: str, stage3_result: Mapping[str, Any]) -> dict[str, Any]:
    numbers = extract_answer_numbers(answer)
    candidates = _candidate_values(stage3_result)
    allowed = [item["value"] for item in candidates]
    unmatched = [item["raw"] for item in numbers if not any(item["value"] == value or abs(item["value"] - value) <= max(abs(value) * Decimal("0.0001"), Decimal("0.01")) for value in allowed)]
    errors = [f"근거에 없는 답변 수치: {value}" for value in unmatched]
    return {
        "pass": not errors,
        "numbers": [{"raw": item["raw"], "normalized": str(item["value"]), "unit": item["unit"]} for item in numbers],
        "matched_count": len(numbers) - len(unmatched),
        "unmatched": unmatched,
        "errors": errors,
    }


__all__ = ["extract_answer_numbers", "validate_numeric_answer"]
