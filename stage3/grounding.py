"""Shared deterministic grounding rules for Stage3 and Stage4."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

from stage3.contracts import Stage3Fact, Stage3Intent


AGGREGATION_SCOPES = frozenset({"total", "segment", "product", "region", "unknown"})

_SEGMENT_CUES = ("사업부문", "부문별", "부문", "segment", "business unit")
_PRODUCT_CUES = ("제품별", "제품", "서비스별", "서비스", "주요 매출원", "product")
_REGION_CUES = ("지역별", "지역", "국가별", "국가", "region")
_TOTAL_CUES = ("연결", "별도", "총계", "합계", "전체", "당사")


def _text(value: object) -> str:
    return str(value or "").strip().lower()


def aggregation_scope_for_context(
    *,
    label: str = "",
    evidence: str = "",
    table_context: Mapping[str, object] | None = None,
) -> str:
    """Classify a numeric Fact using table/row/column and evidence context."""

    context = table_context or {}
    values = [
        label,
        evidence,
        context.get("table_title", ""),
        context.get("section_name", ""),
        context.get("row_label", ""),
        context.get("column_label", ""),
    ]
    joined = " ".join(_text(value) for value in values if _text(value))

    # Explicit total rows take precedence over a table title such as
    # "부문별 매출현황". This keeps a table's total row usable for a total query.
    if any(cue in joined for cue in ("총계", "합계", "전체 합", "소계")):
        return "total"
    if any(cue in joined for cue in _PRODUCT_CUES):
        return "product"
    if any(cue in joined for cue in _REGION_CUES):
        return "region"
    if any(cue in joined for cue in _SEGMENT_CUES):
        return "segment"
    if any(cue in joined for cue in _TOTAL_CUES):
        return "total"
    return "unknown"


def requested_aggregation_scope(intent: Stage3Intent) -> str:
    """Resolve the scope explicitly requested by a question."""

    text = _text(f"{intent.question} {intent.normalized_question}")
    if any(cue in text for cue in _PRODUCT_CUES):
        return "product"
    if any(cue in text for cue in _REGION_CUES):
        return "region"
    if any(cue in text for cue in _SEGMENT_CUES):
        return "segment"
    return "total"


def requested_periods(intent: Stage3Intent) -> list[str]:
    time = intent.time or {}
    years = [str(year) for year in time.get("years") or []]
    months = [int(month) for month in time.get("base_months") or [] if str(month).isdigit()]
    if len(months) == len(years) and years:
        return [f"{year}-{month:02d}" for year, month in zip(years, months)]
    if len(months) == 1 and years:
        return [f"{year}-{months[0]:02d}" for year in years]
    return years


def _period_matches(fact_period: str | None, requested: str) -> bool:
    period = _text(fact_period)
    if not period:
        return False
    return period == requested or (len(requested) == 4 and period.startswith(requested))


def _requested_companies(intent: Stage3Intent) -> set[str]:
    companies = {_text(value) for value in intent.companies if _text(value)}
    manifest = intent.manifest_filter or {}
    companies.update(_text(value) for value in manifest.get("corp_names", []) if _text(value))
    return companies


def _metric_matches(fact: Stage3Fact, intent: Stage3Intent) -> bool:
    requested = _text(intent.metric)
    if not requested:
        return True
    fact_metric = _text(fact.metric)
    label = _text(fact.label)
    if requested == "total_assets":
        question = _text(f"{intent.question} {intent.normalized_question}")
        if "부채비율" in question:
            return fact_metric == "ratio" and "부채비율" in label
        if "자기자본비율" in question:
            return fact_metric == "ratio" and "자기자본비율" in label
        return fact_metric in {"assets", "liabilities", "equity", "ratio"}
    return fact_metric == requested or requested in label


def fact_matches_intent(
    fact: Stage3Fact,
    intent: Stage3Intent,
    *,
    require_scope: bool = True,
) -> bool:
    """Return whether a Fact is safe evidence for the requested lookup."""

    if fact.kind not in {"numeric", "field", "text"}:
        return False
    companies = _requested_companies(intent)
    if companies and _text(fact.company) not in companies:
        return False
    if not _metric_matches(fact, intent):
        return False
    periods = requested_periods(intent)
    if periods and not any(_period_matches(fact.period, period) for period in periods):
        return False
    requested_basis = _text(intent.basis)
    if requested_basis and _text(fact.basis) != requested_basis:
        return False
    if require_scope and fact.kind == "numeric":
        return fact.aggregation_scope == requested_aggregation_scope(intent)
    return True


def matching_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent) -> list[Stage3Fact]:
    return [fact for fact in facts if fact_matches_intent(fact, intent)]


def strict_grounding_enabled() -> bool:
    value = os.getenv("DIS164_STRICT_GROUNDING_V2", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


__all__ = [
    "AGGREGATION_SCOPES",
    "aggregation_scope_for_context",
    "fact_matches_intent",
    "matching_facts",
    "requested_aggregation_scope",
    "requested_periods",
    "strict_grounding_enabled",
]
