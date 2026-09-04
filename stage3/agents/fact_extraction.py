from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from integration.cache import canonical_json, safe_cache_get, safe_cache_put, sha256_text
from stage3.contracts import AgentResult, Stage3Document, Stage3Fact, Stage3Intent
from stage3.deterministic.normalization import UNIT_MULTIPLIERS, normalize_facts, normalize_number
from stage3.metric_registry import (
    METRIC_SPECS,
    fact_metric_for_label,
    field_labels_for,
    numeric_labels_for,
    section_labels_for,
)
from stage3.grounding import aggregation_scope_for_context
from stage3.parsing.structured import parse_structured_evidence
from stage3.state import Stage3GraphState


METRIC_LABELS: dict[str, tuple[str, ...]] = {
    metric: tuple(spec.get("numeric_labels", ()))
    for metric, spec in METRIC_SPECS.items()
    if spec.get("numeric_labels")
}

ALL_LABELS = tuple(dict.fromkeys(label for labels in METRIC_LABELS.values() for label in labels))
UNIT_PATTERN = r"조원|조|십억원|억원|백만원|천만원|만원|천원|원|%"
NUMBER_PATTERN = r"(?:△|▲|-)?\s*\d[\d,]*(?:\.\d+)?"
DATE_PATTERN = re.compile(r"(?:20\d{2}년\s*\d{1,2}월\s*\d{1,2}일?|20\d{2}[-./]\s*\d{1,2}[-./]\s*\d{1,2}|20\d{2}년\s*(?:[1-4]분기|상반기|하반기|연간)|20\d{2}년(?!\s*\d{1,2}월))")
_SPACED_HANGUL_RE = re.compile(r"(?<![가-힣])(?:[가-힣]\s+){2,}[가-힣](?![가-힣])")


def _normalize_disclosure_spacing(value: str) -> str:
    """Join Hangul labels split into one-character cells by DART conversion."""

    return _SPACED_HANGUL_RE.sub(
        lambda match: re.sub(r"\s+", "", match.group(0)),
        value,
    )


def _period_from_table_context(context: dict, metadata: dict) -> str | None:
    column_label = re.sub(r"\s+", "", str(context.get("column_label") or ""))
    # DART fiscal-year headers ("제56기(2024)", "제55기(2023)") carry the
    # actual year in parentheses. Read it directly instead of guessing from
    # the document's own base year — a 전기/전전기 column otherwise inherits
    # the current document's period and gets misattributed to the wrong year.
    explicit_year = re.search(r"20\d{2}", column_label)
    if explicit_year:
        return explicit_year.group(0)
    year = metadata.get("base_year")
    if year is None:
        return None
    try:
        year_value = int(float(year))
    except (TypeError, ValueError):
        return None
    if "전전" in column_label:
        return str(year_value - 2)
    if "전" in column_label:
        return str(year_value - 1)
    if "당" in column_label:
        return str(year_value)
    return None


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


def _metric_pattern(metric: str | None, *, extra_metrics: Iterable[str] = ()) -> tuple[str, ...]:
    labels = list(numeric_labels_for(metric))
    for extra_metric in extra_metrics:
        labels.extend(numeric_labels_for(extra_metric))
    return tuple(dict.fromkeys(labels))


def _parse_numeric(value: str) -> float:
    cleaned = value.replace(",", "").replace(" ", "")
    negative = cleaned.startswith(("△", "▲", "-"))
    cleaned = cleaned.lstrip("△▲-")
    number = float(cleaned)
    return -number if negative else number


_GROUPED_NUMBER = re.compile(r"(?:△|▲|-)?\s*\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def _plausible_numeric_literal(value: str) -> bool:
    """Reject glued table cells such as ``1,852,115579,183`` as one number."""

    cleaned = value.replace(" ", "").lstrip("△▲-")
    if not cleaned or cleaned.count(".") > 1:
        return False
    integer, _, fraction = cleaned.partition(".")
    groups = integer.split(",")
    digits = integer.replace(",", "")
    if not digits.isdigit() or (fraction and not fraction.isdigit()):
        return False
    if len(digits) > 15:
        return False
    if "," in integer:
        if not (1 <= len(groups[0]) <= 3):
            return False
        if any(len(part) != 3 for part in groups[1:]):
            return False
    return True


def _numeric_literals(value: str) -> list[str]:
    """Return plausible numbers, splitting DART cells that were concatenated."""

    cleaned = value.replace(" ", "")
    if _plausible_numeric_literal(cleaned):
        return [cleaned]
    grouped = [match.group(0) for match in _GROUPED_NUMBER.finditer(cleaned) if _plausible_numeric_literal(match.group(0))]
    return grouped


def _default_statement_unit(label: str, metric: str, unit: str, raw_literal: str, raw_value: float) -> str:
    """Fill DART statement units when the unit row was dropped from a chunk."""

    if unit:
        return unit
    if metric == "ratio" or "비율" in label or "율" in label or "%" in label:
        return "%"
    if "," in raw_literal and 1e5 <= abs(raw_value) < 1e12:
        return "백만원"
    return unit


def _pick_operating_profit_literal(literals: list[str]) -> list[str]:
    """Prefer a quarterly/annual 영업이익 amount over glued table fragments."""

    plausible: list[tuple[float, str]] = []
    for raw_literal in literals:
        value = abs(_parse_numeric(raw_literal))
        if 1e3 <= value <= 30_000_000:
            plausible.append((value, raw_literal))
    if plausible:
        plausible.sort()
        return [plausible[len(plausible) // 2][1]]
    return literals[:1]


_COMPOUND_TAIL = re.compile(rf"\s*(?P<minor>{NUMBER_PATTERN})\s*억\s*(?:원)?")


def _compound_amount(
    source_text: str,
    match: re.Match[str],
) -> tuple[float, str, int] | None:
    """Parse a major 조 amount followed by a minor 억 amount."""

    if match.group("unit") not in {"조", "조원"}:
        return None
    tail = _COMPOUND_TAIL.match(source_text, match.end())
    if tail is None:
        return None
    major = _parse_numeric(match.group("value"))
    minor = _parse_numeric(tail.group("minor"))
    sign = -1.0 if major < 0 or minor < 0 else 1.0
    canonical = sign * (abs(major) * 1_000_000_000_000 + abs(minor) * 100_000_000)
    display = source_text[match.start("value") : tail.end()].strip()
    return canonical, display, tail.end()


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


FACT_EXTRACTOR_VERSION = "fact-extractor-v1"


def _extract_facts_uncached(
    documents: Iterable[Stage3Document],
    intent: Stage3Intent,
    *,
    structured_cache: Any | None = None,
) -> list[Stage3Fact]:
    """Extract grounded numeric and date facts from Stage2 evidence text."""

    facts: list[Stage3Fact] = []
    extra_metrics: list[str] = []
    operation = str(intent.calculation.get("operation", ""))
    if operation in {"ratio_percent", "margin"}:
        extra_metrics.append(str(intent.calculation.get("denominator_metric") or "revenue"))
    if operation == "margin":
        extra_metrics.append("operating_profit")
    labels = _metric_pattern(intent.metric, extra_metrics=extra_metrics)
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True)) or r"(?!)"
    # Do not treat a metric prefix as a standalone label.  For example,
    # ``매출채권`` must not become a ``매출`` Fact.  Korean case particles
    # (such as ``매출액은``) remain valid after the metric label.
    numeric_pattern = re.compile(
        rf"(?P<label>{label_pattern})(?!채권|원가|총이익)"
        rf"(?:(?!(?:{label_pattern}))[^\d\-△▲]){{0,80}}"
        rf"(?P<value>{NUMBER_PATTERN})\s*(?P<unit>{UNIT_PATTERN}|단위)?"
    )
    for document in documents:
        structured = parse_structured_evidence(
            document.text or "",
            cache=structured_cache,
            document_id=document.id,
        )
        text = _normalize_disclosure_spacing(structured.text)
        metadata = document.metadata
        company = metadata.get("corp_name") or (intent.companies[0] if len(intent.companies) == 1 else None)
        # Once a structured table is available, extract numeric facts from
        # its cell context instead of scanning the rendered table wholesale.
        # The latter can associate a footnote number (for example ``29``)
        # with the first metric on the row.
        numeric_sources: list[tuple[str, dict]] = [] if structured.has_structured_tables else [(text, {})]
        for cell in structured.numeric_cells:
            if structured.source_format == "markdown":
                cell_source = f"{cell.get('row_label', '')} | {cell.get('value', '')}"
            else:
                cell_source = f"{cell.get('row_label', '')} | {cell.get('column_label', '')} | {cell.get('value', '')}"
            numeric_sources.append(
                (
                    _normalize_disclosure_spacing(cell_source),
                    cell,
                )
            )
        numeric_facts: list[Stage3Fact] = []
        numeric_index: dict[tuple[str, str, str, str | None, str], int] = {}
        for source_text, context in numeric_sources:
            for match in numeric_pattern.finditer(source_text):
                label = match.group("label")
                raw_literals = _numeric_literals(match.group("value").strip())
                if intent.metric == "operating_profit" and "영업이익" in label and "률" not in label and len(raw_literals) > 1:
                    raw_literals = _pick_operating_profit_literal(raw_literals)
                elif len(raw_literals) > 1:
                    raw_literals = raw_literals[:1]
                for raw_literal in raw_literals:
                    raw_value = _parse_numeric(raw_literal)
                    unit = match.group("unit") or str(context.get("unit") or "")
                    compound = _compound_amount(source_text, match)
                    display_value = None
                    if compound is not None:
                        raw_value, display_value, compound_end = compound
                        unit = "원"
                    fact_raw_value = display_value or raw_literal
                    if not unit and "원" in match.group("label"):
                        unit = "원"
                    if not unit and ("%" in match.group("label") or "비율" in match.group("label") or "율" in match.group("label")):
                        unit = "%"
                    metric = fact_metric_for_label(label, intent.metric)
                    unit = _default_statement_unit(label, metric, unit, raw_literal, raw_value)
                    if (
                        intent.metric == "operating_profit"
                        and metric == "operating_profit"
                        and unit == "백만원"
                        and abs(raw_value) > 30_000_000
                    ):
                        continue
                    evidence = source_text.strip() if context else _evidence(text, match.start(), match.end())
                    period = (
                        context.get("period_label")
                        or _period_from_table_context(context, metadata)
                        or _period(evidence, metadata)
                        or _period(text, metadata)
                    )
                    basis = context.get("basis") or _basis(evidence, metadata, intent)
                    scope_context = {**context, "basis": basis}
                    fact = Stage3Fact(
                        metric=metric,
                        label=match.group("label"),
                        value=raw_value,
                        raw_value=fact_raw_value,
                        unit=unit,
                        normalized_value=normalize_number(raw_value, unit),
                        period=period,
                        basis=basis,
                        company=str(company) if company else None,
                        document_id=document.id,
                        source=document.source,
                        evidence=evidence,
                        span_start=None if context else match.start(),
                        span_end=None if context else (compound_end if compound is not None else match.end()),
                        confidence=0.95 if context else (0.9 if unit else 0.7),
                        currency=_fact_currency(unit, context, metadata),
                        table_context=dict(context),
                        aggregation_scope=aggregation_scope_for_context(
                            label=match.group("label"),
                            evidence=evidence,
                            table_context=scope_context,
                        ),
                        display_value=display_value,
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


def _fact_cache_profile(document: Stage3Document, intent: Stage3Intent) -> dict[str, Any]:
    calculation = intent.calculation if isinstance(intent.calculation, Mapping) else {}
    companies = tuple(getattr(intent, "companies", ()) or ())
    metadata_company = str(document.metadata.get("corp_name") or "").strip()
    return {
        "document_id": document.id,
        "text_sha256": sha256_text(document.text or ""),
        "extractor_version": FACT_EXTRACTOR_VERSION,
        "metric": str(intent.metric or ""),
        "operation": str(calculation.get("operation") or ""),
        "denominator_metric": str(calculation.get("denominator_metric") or ""),
        "basis": str(intent.basis or ""),
        # The fallback is only relevant when document metadata has no company.
        "company_fallback": (
            str(companies[0]) if len(companies) == 1 and not metadata_company else ""
        ),
    }


def extract_facts(
    documents: Iterable[Stage3Document],
    intent: Stage3Intent,
    *,
    cache: Any | None = None,
) -> list[Stage3Fact]:
    """Extract facts, reusing only compatible document/profile results."""

    document_list = list(documents)
    if cache is None:
        return _extract_facts_uncached(document_list, intent)

    facts: list[Stage3Fact] = []
    fact_cache = getattr(cache, "fact_results", None)
    for document in document_list:
        cache_key = "fact-results:" + canonical_json(_fact_cache_profile(document, intent))
        cached = safe_cache_get(fact_cache, cache_key)
        if cached is None:
            cached = _extract_facts_uncached(
                [document],
                intent,
                structured_cache=cache,
            )
            safe_cache_put(fact_cache, cache_key, cached)
        facts.extend(cached)
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
