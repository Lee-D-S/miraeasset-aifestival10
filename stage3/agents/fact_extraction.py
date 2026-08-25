from __future__ import annotations

import re
from typing import Iterable

from stage3.contracts import Stage3Document, Stage3Fact, Stage3Intent
from stage3.deterministic.normalization import UNIT_MULTIPLIERS, normalize_number
from stage3.parsing.structured import parse_structured_evidence


METRIC_LABELS: dict[str, tuple[str, ...]] = {
    "revenue": ("매출액", "매출"),
    "operating_income": ("영업이익",),
    "net_income": ("당기순이익", "순이익"),
    "assets": ("자산총계", "총자산"),
    "liabilities": ("부채총계", "총부채"),
    "equity": ("자본총계", "총자본"),
    "current_assets": ("유동자산",),
    "current_liabilities": ("유동부채",),
    "capex": ("설비투자", "시설투자", "신규시설투자"),
    "funding": ("조달금액", "자금조달", "발행금액"),
}

ALL_LABELS = tuple(dict.fromkeys(label for labels in METRIC_LABELS.values() for label in labels))
UNIT_PATTERN = r"조원|십억원|억원|백만원|천만원|만원|천원|원|%"
NUMBER_PATTERN = r"(?:△|▲|-)?\s*\d[\d,]*(?:\.\d+)?"
DATE_PATTERN = re.compile(r"(?:20\d{2}년\s*\d{1,2}월\s*\d{1,2}일?|20\d{2}[-./]\s*\d{1,2}[-./]\s*\d{1,2}|20\d{2}년\s*(?:[1-4]분기|상반기|하반기|연간)|20\d{2}년(?!\s*\d{1,2}월))")


def _period(text: str, metadata: dict) -> str | None:
    report_period = metadata.get("report_period")
    if report_period:
        return str(report_period)
    year = metadata.get("base_year")
    month = metadata.get("base_month")
    if year and month:
        return f"{year}-{int(month):02d}"
    match = re.search(r"(20\d{2})\s*년\s*(?:(1|2|3|4)\s*분기|연간|사업보고서|반기)", text)
    if match:
        if match.group(2):
            return f"{match.group(1)}-{int(match.group(2)) * 3:02d}"
        return match.group(1)
    year_match = re.search(r"20\d{2}", text)
    return year_match.group(0) if year_match else None


def _basis(text: str, metadata: dict, intent: Stage3Intent) -> str | None:
    if "연결" in text:
        return "연결"
    if "별도" in text:
        return "별도"
    return str(metadata.get("basis")) if metadata.get("basis") else intent.basis


def _evidence(text: str, start: int, end: int) -> str:
    left = max(text.rfind("\n", 0, start), text.rfind(".", 0, start), text.rfind("다.", 0, start)) + 1
    right_candidates = [item for item in (text.find("\n", end), text.find(".", end)) if item >= 0]
    right = min(right_candidates) + 1 if right_candidates else min(len(text), end + 180)
    return text[left:right].strip()


def _metric_pattern(metric: str | None) -> tuple[str, ...]:
    if metric and metric in METRIC_LABELS:
        return METRIC_LABELS[metric]
    return ALL_LABELS


def _parse_numeric(value: str) -> float:
    cleaned = value.replace(",", "").replace(" ", "")
    negative = cleaned.startswith(("△", "▲", "-"))
    cleaned = cleaned.lstrip("△▲-")
    number = float(cleaned)
    return -number if negative else number


def _fact_currency(unit: str, context: dict, metadata: dict) -> str | None:
    if context.get("currency"):
        return str(context["currency"])
    if metadata.get("currency"):
        return str(metadata["currency"])
    if unit in UNIT_MULTIPLIERS and unit not in {"", "%"}:
        return "KRW"
    return None


def extract_facts(documents: Iterable[Stage3Document], intent: Stage3Intent) -> list[Stage3Fact]:
    """Extract grounded numeric and date facts from Stage2 evidence text."""

    facts: list[Stage3Fact] = []
    labels = _metric_pattern(intent.metric)
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    numeric_pattern = re.compile(rf"(?P<label>{label_pattern})[^\d\-△▲]*(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{UNIT_PATTERN}|단위)?")
    for document in documents:
        structured = parse_structured_evidence(document.text or "")
        text = structured.text
        metadata = document.metadata
        company = metadata.get("corp_name") or (intent.companies[0] if len(intent.companies) == 1 else None)
        numeric_sources: list[tuple[str, dict]] = [(text, {})]
        for cell in structured.numeric_cells:
            numeric_sources.append(
                (
                    f"{cell.get('row_label', '')} | {cell.get('column_label', '')} | {cell.get('value', '')}",
                    cell,
                )
            )
        numeric_facts: list[Stage3Fact] = []
        numeric_index: dict[tuple[str, str, str, str | None, str], int] = {}
        for source_text, context in numeric_sources:
            for match in numeric_pattern.finditer(source_text):
                raw_literal = match.group("value").strip()
                raw_value = _parse_numeric(raw_literal)
                unit = match.group("unit") or str(context.get("unit") or "")
                metric = next((key for key, values in METRIC_LABELS.items() if match.group("label") in values), intent.metric or "unknown")
                evidence = source_text.strip() if context else _evidence(text, match.start(), match.end())
                period = context.get("period_label") or _period(evidence, metadata) or _period(text, metadata)
                basis = context.get("basis") or _basis(evidence, metadata, intent)
                fact = Stage3Fact(
                    metric=metric,
                    label=match.group("label"),
                    value=raw_value,
                    raw_value=raw_literal,
                    unit=unit,
                    normalized_value=normalize_number(raw_value, unit),
                    period=period,
                    basis=basis,
                    company=str(company) if company else None,
                    document_id=document.id,
                    source=document.source,
                    evidence=evidence,
                    span_start=None if context else match.start(),
                    span_end=None if context else match.end(),
                    confidence=0.95 if context else (0.9 if unit else 0.7),
                    currency=_fact_currency(unit, context, metadata),
                    table_context=dict(context),
                )
                key = (document.id, metric, match.group("label"), period, raw_literal)
                existing_index = numeric_index.get(key)
                if existing_index is None:
                    numeric_index[key] = len(numeric_facts)
                    numeric_facts.append(fact)
                elif fact.confidence > numeric_facts[existing_index].confidence:
                    numeric_facts[existing_index] = fact
        facts.extend(numeric_facts)
        for match in DATE_PATTERN.finditer(text):
            evidence = _evidence(text, match.start(), match.end())
            facts.append(Stage3Fact(
                metric="date",
                label="날짜/기간",
                value=match.group(0),
                raw_value=match.group(0),
                unit="",
                normalized_value=match.group(0),
                period=_period(evidence, metadata),
                basis=_basis(evidence, metadata, intent),
                company=str(company) if company else None,
                document_id=document.id,
                source=document.source,
                evidence=evidence,
                span_start=match.start(),
                span_end=match.end(),
                confidence=0.85,
                kind="date",
            ))
    return facts
