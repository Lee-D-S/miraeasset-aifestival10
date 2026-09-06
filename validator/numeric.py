from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from reasoner.contracts import ReasonerFact, ReasonerIntent
from reasoner.grounding import fact_matches_intent


_NUMBER = re.compile(r"(?<![\w])[-+]?\d[\d,]*(?:\.\d+)?")
# Fact/answer periods render as "YYYY-MM" or "YYYY-MM-DD" (see
# reasoner.agents.answer._lookup_claim). The "년/월/일" suffix check below only
# catches the Korean-suffixed form, so a hyphenated period would otherwise be
# read as two spurious answer numbers ("2024" and "12").
_ISO_PERIOD_RE = re.compile(r"(?<!\d)20\d{2}-(?:0[1-9]|1[0-2])(?:-(?:0[1-9]|[12]\d|3[01]))?(?!\d)")
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


# DART amounts routinely chain 조 and 억 into one figure ("300조 8,709억원").
# Checked as two independent numbers, neither half equals the combined Fact
# value, so a correct answer would fail the numeric gate. Match the trailing
# 억 amount right after a 조 amount and fold both into a single value —
# mirrors reasoner.agents.fact_extraction._compound_amount, which does the same
# for the source-document side of this parsing.
_COMPOUND_TAIL = re.compile(r"조원?\s*(?P<minor>[-+]?\d[\d,]*(?:\.\d+)?)\s*억\s*원?")


def _compound_amount(answer: str, end: int) -> tuple[Decimal, int] | None:
    tail = _COMPOUND_TAIL.match(answer, end)
    if tail is None:
        return None
    minor = _decimal(tail.group("minor"))
    if minor is None:
        return None
    return minor, tail.end()


def extract_answer_numbers(answer: str) -> list[dict[str, Any]]:
    """Extract answer numbers while ignoring dates, ranks, and document IDs."""

    values: list[dict[str, Any]] = []
    matches = list(_NUMBER.finditer(answer))
    period_spans = [span.span() for span in _ISO_PERIOD_RE.finditer(answer)]
    index = 0
    while index < len(matches):
        match = matches[index]
        index += 1
        start, end = match.span()
        if any(period_start <= start and end <= period_end for period_start, period_end in period_spans):
            continue
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
        compound = _compound_amount(answer, end) if unit == "조" else None
        if compound is not None:
            minor, tail_end = compound
            values.append({
                "raw": answer[start:tail_end],
                "value": base * multiplier + minor * _UNIT_MULTIPLIERS["억"],
                "unit": "조",
                "percent": False,
                "start": start,
                "end": tail_end,
            })
            while index < len(matches) and matches[index].start() < tail_end:
                index += 1
            continue
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


def _candidate_values(
    reasoner_result: Mapping[str, Any], intent: ReasonerIntent | None = None
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for fact in reasoner_result.get("facts", []):
        if not isinstance(fact, Mapping):
            continue
        # Trusting whatever Reasoner extracted (regardless of the requested
        # company/period/basis/aggregation scope) is what let a mistagged
        # breakdown-row Fact validate a wrong headline number before. Re-check
        # the Fact against the intent here instead of only in validator/node.py's
        # separate "requested Fact gate".
        if intent is not None and not fact_matches_intent(ReasonerFact.from_dict(fact), intent):
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
        for item in reasoner_result.get(group, []):
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


def validate_numeric_answer(
    answer: str, reasoner_result: Mapping[str, Any], intent: ReasonerIntent | None = None
) -> dict[str, Any]:
    numbers = extract_answer_numbers(answer)
    candidates = _candidate_values(reasoner_result, intent)
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
