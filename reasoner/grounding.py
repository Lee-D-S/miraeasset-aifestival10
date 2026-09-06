"""Shared deterministic grounding rules for Reasoner and Validator."""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping

from reasoner.contracts import ReasonerFact, ReasonerIntent


AGGREGATION_SCOPES = frozenset({"total", "segment", "product", "region", "not_total", "unknown"})

_SEGMENT_CUES = ("사업부문", "부문별", "부문", "segment", "business unit")
_PRODUCT_CUES = ("제품별", "제품", "서비스별", "서비스", "주요 매출원", "product")
_REGION_CUES = ("지역별", "지역", "국가별", "국가", "region")
_TOTAL_CUES = ("연결", "별도", "총계", "합계", "전체", "당사")
_NON_TOTAL_CUES = ("기타매출", "용역 및", "매출유형", "매출 유형")


def _text(value: object) -> str:
    return str(value or "").strip().lower()


_ROW_LABEL_PREFIX_RE = re.compile(r"^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩivx0-9]+[.)．]*")


def _normalize_row_label(value: object) -> str:
    """Strip DART letter-spacing ("매    출    액") and numbering ("Ⅰ.") so a
    bare total row still matches the label the regex extracted."""

    collapsed = re.sub(r"\s+", "", str(value or ""))
    return _ROW_LABEL_PREFIX_RE.sub("", collapsed).strip(".)．").lower()


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
        context.get("table_scope", ""),
        context.get("basis", ""),
        context.get("basis_label", ""),
    ]
    joined = " ".join(_text(value) for value in values if _text(value))

    # A table/section title naming a revenue-type breakdown (e.g. "매출유형별
    # 현황") is a known non-total signal even when the row itself has no
    # row_label to compare against the extracted metric label.
    if any(cue in joined for cue in _NON_TOTAL_CUES):
        return "not_total"
    # Forward-looking capex/investment plan tables use year headers and
    # "합계" without being company-wide revenue totals.
    if any(cue in joined for cue in ("잔여 계획기간", "계획기간", "합계 구간", "당기 이행연도")):
        return "not_total"
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
    # "연결"/"별도"/"당사" describe the statement basis, not the row's
    # aggregation level. A row with its own descriptive label (for example
    # "용역 및 기타매출") is a breakdown line even inside a 연결 statement, so
    # only fall back to the basis cues when the row itself is a bare metric
    # or an explicit subtotal row (or there is no row context at all, as for
    # plain-text evidence).
    row_label_text = _text(context.get("row_label"))
    normalized_row_label = _normalize_row_label(context.get("row_label"))
    is_bare_metric_row = (
        not row_label_text
        or normalized_row_label == _normalize_row_label(label)
        or normalized_row_label in {"계", "소계", "합계", "총계"}
    )
    if is_bare_metric_row and any(cue in joined for cue in _TOTAL_CUES):
        return "total"
    # A row that carries its own label but was not identified as a total is
    # a known breakdown line ("not_total"), distinct from evidence that has
    # no row context to judge at all ("unknown"). Only the latter should let
    # a strict "total" request through — see fact_matches_intent.
    return "unknown" if not row_label_text else "not_total"


def requested_aggregation_scope(intent: ReasonerIntent) -> str:
    """Resolve the scope explicitly requested by a question."""

    text = _text(f"{intent.question} {intent.normalized_question}")
    if any(cue in text for cue in _PRODUCT_CUES):
        return "product"
    if any(cue in text for cue in _REGION_CUES):
        return "region"
    if any(cue in text for cue in _SEGMENT_CUES):
        return "segment"
    return "total"


def requested_periods(intent: ReasonerIntent) -> list[str]:
    time = intent.time or {}
    years = [str(year) for year in time.get("years") or []]
    months = [int(month) for month in time.get("base_months") or [] if str(month).isdigit()]
    if len(months) == len(years) and years:
        return [f"{year}-{month:02d}" for year, month in zip(years, months)]
    if len(months) == 1 and years:
        return [f"{year}-{months[0]:02d}" for year in years]
    return years


def _normalize_period_key(value: object) -> str:
    """Normalize Korean quarter/year labels to the API's YYYY[-MM] form."""

    text = str(value or "").strip()
    if not text:
        return ""
    quarter = re.search(r"(\d{4})\s*년\s*([1-4])\s*분기", text)
    if quarter:
        return f"{quarter.group(1)}-{int(quarter.group(2)) * 3:02d}"
    year_month = re.search(r"(\d{4})\s*[-/.]\s*(\d{1,2})", text)
    if year_month:
        return f"{year_month.group(1)}-{int(year_month.group(2)):02d}"
    year = re.search(r"(\d{4})\s*년", text)
    if year:
        return year.group(1)
    return text


def period_matches(fact_period: str | None, requested: str) -> bool:
    """Match a Fact period to a Interpreter requested period.

    Annual lookups often emit ``2025-12`` while a table cell only carries
    ``2025``. Treat same-year year vs year-month as a match, but keep
    distinct months (``2025-03`` vs ``2025-12``) distinct.
    """

    period = _normalize_period_key(fact_period)
    wanted = _normalize_period_key(requested)
    if not period or not wanted:
        return False
    if period == wanted:
        return True
    period_year, wanted_year = period[:4], wanted[:4]
    if period_year != wanted_year or not period_year.isdigit():
        return False
    return len(period) == 4 or len(wanted) == 4


def _requested_companies(intent: ReasonerIntent) -> set[str]:
    companies = {_text(value) for value in intent.companies if _text(value)}
    manifest = intent.manifest_filter or {}
    companies.update(_text(value) for value in manifest.get("corp_names", []) if _text(value))
    return companies


def _metric_matches(fact: ReasonerFact, intent: ReasonerIntent) -> bool:
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
    fact: ReasonerFact,
    intent: ReasonerIntent,
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
    if periods and not any(period_matches(fact.period, period) for period in periods):
        return False
    requested_basis = _text(intent.basis)
    if requested_basis and _text(fact.basis) != requested_basis:
        return False
    if require_scope and fact.kind == "numeric":
        requested_scope = requested_aggregation_scope(intent)
        if fact.aggregation_scope == requested_scope:
            return True
        # Ratio/amount cells often carry no row context at all, so scope
        # stays "unknown" rather than a judged "not_total". Only that true
        # no-context case should pass a strict total request — a Fact with
        # its own row label that was judged not-total must stay excluded.
        return fact.aggregation_scope == "unknown" and requested_scope == "total"
    return True


def matching_facts(
    facts: Iterable[ReasonerFact],
    intent: ReasonerIntent,
    *,
    require_scope: bool = True,
) -> list[ReasonerFact]:
    return [fact for fact in facts if fact_matches_intent(fact, intent, require_scope=require_scope)]


def select_segment_facts(
    facts: Iterable[ReasonerFact], *, limit: int = 20
) -> list[ReasonerFact]:
    """Select the table-backed amount rows for a segment answer.

    A retrieval set can contain narrative totals and percentage cells alongside
    the requested segment rows.  When structured rows exist, they are the
    authoritative answer candidates.  The selection is cardinality-agnostic
    and deduplicates repeated evidence before applying the same limit used by
    the answer writer.
    """

    pool = [fact for fact in facts if fact.unit != "%"]
    numeric_pool = [fact for fact in pool if fact.kind == "numeric"]
    table_numeric_pool = [fact for fact in numeric_pool if fact.table_context]
    if table_numeric_pool:
        pool = table_numeric_pool
    elif numeric_pool:
        pool = numeric_pool

    unique: dict[tuple[str, str, str], ReasonerFact] = {}
    for fact in pool:
        row_label = str(fact.table_context.get("row_label") or fact.label or "")
        value = str(fact.normalized_value if fact.normalized_value is not None else fact.value)
        key = (row_label, str(fact.period or ""), value)
        unique.setdefault(key, fact)
    return sorted(
        unique.values(),
        key=lambda fact: (
            str(fact.table_context.get("row_label") or fact.label or ""),
            str(fact.period or ""),
            str(fact.document_id or ""),
        ),
    )[: max(int(limit), 1)]


def strict_grounding_enabled() -> bool:
    value = os.getenv("DIS164_STRICT_GROUNDING_V2", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


__all__ = [
    "AGGREGATION_SCOPES",
    "aggregation_scope_for_context",
    "fact_matches_intent",
    "matching_facts",
    "select_segment_facts",
    "period_matches",
    "requested_aggregation_scope",
    "requested_periods",
    "strict_grounding_enabled",
]
