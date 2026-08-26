from __future__ import annotations

from typing import Any

from stage3.contracts import Stage3Intent


SUPPORTED_OPERATIONS = frozenset({
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
    """Plan an operation from Stage1 intent without reclassifying the question."""

    question = intent.normalized_question
    if intent.intent in {"compare", "comparison"} or any(word in question for word in ("비교", "어느 기업", "가장 큰", "순위")):
        return {"operation": "rank", "metric": intent.metric}
    if any(word in question for word in ("증감률", "증가율", "감소율", "성장률")):
        return {"operation": "percentage_change", "metric": intent.metric}
    if "CAGR" in question.upper() or "연평균" in question:
        return {"operation": "cagr", "metric": intent.metric}
    if "영업이익률" in question or "마진" in question:
        return {"operation": "margin", "metric": intent.metric, "denominator_metric": "revenue"}
    if "비중" in question or "비율" in question:
        return {"operation": "ratio_percent", "metric": intent.metric, "denominator_metric": "revenue"}
    if "합계" in question or "총액" in question:
        return {"operation": "sum", "metric": intent.metric}
    return None


__all__ = ["SUPPORTED_OPERATIONS", "build_calculation_plan"]
