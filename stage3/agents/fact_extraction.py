from __future__ import annotations

import re
from typing import Iterable

from stage3.contracts import AgentResult, Stage3Document, Stage3Fact, Stage3Intent
from stage3.deterministic.normalization import UNIT_MULTIPLIERS, normalize_facts, normalize_number
from stage3.metric_registry import (
    METRIC_SPECS,
    fact_metric_for_label,
    field_labels_for,
    numeric_labels_for,
    section_labels_for,
)
from stage3.parsing.structured import parse_structured_evidence
from stage3.state import Stage3GraphState


METRIC_LABELS: dict[str, tuple[str, ...]] = {
    metric: tuple(spec.get("numeric_labels", ()))
    for metric, spec in METRIC_SPECS.items()
    if spec.get("numeric_labels")
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


def _metric_pattern(metric: str | None, question: str = "") -> tuple[str, ...]:
    labels = list(numeric_labels_for(metric))
    if any(word in question for word in ("비중", "비율", "마진", "영업이익률")):
        labels.extend(numeric_labels_for("revenue"))
    if "영업이익률" in question or "마진" in question:
        labels.extend(numeric_labels_for("operating_profit"))
    return tuple(dict.fromkeys(labels))


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


def _field_segments(
    text: str,
    field_labels: dict[str, tuple[str, ...]],
    boundary_labels: tuple[str, ...] = (),
) -> list[tuple[str, str]]:
    aliases: list[tuple[str, str]] = []
    for field, labels in field_labels.items():
        aliases.extend((label, field) for label in labels)
    if not aliases:
        return []
    all_labels = list({label for label, _field in aliases} | set(boundary_labels))
    label_pattern = "|".join(re.escape(label) for label in sorted(all_labels, key=len, reverse=True))
    alias_to_field = dict(aliases)
    matches = list(re.finditer(rf"(?P<label>{label_pattern})\s*[:：]?", text))
    segments: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        value = text[match.end():end].strip(" |\t\r\n:：-")
        value = re.sub(r"\s+", " ", value).strip()
        if value and match.group("label") in alias_to_field:
            segments.append((alias_to_field[match.group("label")], value))
    return segments


def _text_fact(
    *,
    metric: str,
    label: str,
    value: str,
    document: Stage3Document,
    metadata: dict,
    company: Any,
    intent: Stage3Intent,
    evidence: str,
    kind: str,
) -> Stage3Fact:
    return Stage3Fact(
        metric=metric,
        label=label,
        value=value,
        raw_value=value,
        unit="",
        normalized_value=value,
        period=_period(evidence, metadata),
        basis=_basis(evidence, metadata, intent),
        company=str(company) if company else None,
        document_id=document.id,
        source=document.source,
        evidence=evidence,
        confidence=0.8,
        kind=kind,
    )


def extract_facts(documents: Iterable[Stage3Document], intent: Stage3Intent) -> list[Stage3Fact]:
    """Extract grounded numeric and date facts from Stage2 evidence text."""

    facts: list[Stage3Fact] = []
    labels = _metric_pattern(intent.metric, intent.normalized_question)
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True)) or r"(?!)"
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
                if not unit and "원" in match.group("label"):
                    unit = "원"
                if not unit and ("%" in match.group("label") or "비율" in match.group("label") or "율" in match.group("label")):
                    unit = "%"
                metric = fact_metric_for_label(match.group("label"), intent.metric)
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
        field_specs = field_labels_for(intent.metric)
        field_sources = [text]
        field_sources.extend(table.rendered_text for table in structured.tables)
        seen_fields: set[tuple[str, str]] = set()
        for field_source in field_sources:
            for field, value in _field_segments(field_source, field_specs, numeric_labels_for(intent.metric)):
                key = (field, value)
                if key in seen_fields:
                    continue
                seen_fields.add(key)
                facts.append(
                    _text_fact(
                        metric=intent.metric or "unknown",
                        label=field,
                        value=value,
                        document=document,
                        metadata=metadata,
                        company=company,
                        intent=intent,
                        evidence=value,
                        kind="field",
                    )
                )
        section_labels = section_labels_for(intent.metric)
        for section_label in section_labels:
            section_start = text.find(section_label)
            if section_start < 0:
                continue
            section_evidence = text[section_start:section_start + 240].strip()
            facts.append(
                _text_fact(
                    metric=intent.metric or "unknown",
                    label=section_label,
                    value=section_evidence,
                    document=document,
                    metadata=metadata,
                    company=company,
                    intent=intent,
                    evidence=section_evidence,
                    kind="text",
                )
            )
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


def fact_extraction_agent(state: Stage3GraphState) -> AgentResult:
    intent = state["intent"]
    facts = extract_facts(state.get("documents", []), intent)
    normalized, warnings = normalize_facts(facts, intent)
    evidence_ids = tuple(dict.fromkeys(fact.document_id for fact in normalized if fact.document_id))
    return AgentResult(
        agent="fact_extractor",
        status="ok" if normalized else "empty",
        facts=tuple(fact.to_dict() for fact in normalized),
        evidence_ids=evidence_ids,
        confidence=max((fact.confidence for fact in normalized), default=0.0),
        warnings=tuple(warnings),
        trace=(f"documents={len(state.get('documents', []))}", f"facts={len(normalized)}"),
    )
