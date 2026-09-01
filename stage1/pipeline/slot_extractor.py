"""③ 슬롯 추출 — 연도·기간·공시유형·지표·intent를 규칙으로 채운다.

시간은 두 축을 구분한다.
- fiscal      : 정기공시 보고기간 → base_year / base_month
- disclosure  : 수시공시 접수일   → rcept_dt 범위
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..index.corpus_index import CorpusIndex, squash
from .entity_linker import EntityResult
from .preprocess import PreprocessResult

_YEAR4 = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
_YEAR2 = re.compile(r"(?<!\d)(\d{2})\s*년")
_FY = re.compile(r"fy\s*((?:19|20)?\d{2})", re.IGNORECASE)
# 진짜 범위 표현만 확장한다. "2023년과 2025년"은 두 시점 비교이므로 사이 연도를 넣지 않는다.
_RANGE = re.compile(r"((?:19|20)\d{2})\s*년?\s*(?:~|부터|에서)\s*((?:19|20)\d{2})\s*년?")
_QUARTER = re.compile(r"(?<!\d)([1-4])\s*(?:/\s*4)?\s*분기")
_QUARTER_Q = re.compile(r"(?<![a-z0-9])([1-4])\s*q(?![a-z])", re.IGNORECASE)
_QUARTER_ALT = re.compile(r"(?<![a-z0-9])q\s*([1-4])(?!\d)", re.IGNORECASE)

_FUNDRAISING_INSTRUMENTS: dict[str, list[str]] = {
    "유상증자": ["유상증자결정"],
    "무상증자": ["무상증자결정"],
    "전환사채": ["전환사채권발행결정"],
    "cb": ["전환사채권발행결정"],
    "교환사채": ["교환사채권발행결정"],
    "eb": ["교환사채권발행결정"],
    "신주인수권부사채": ["신주인수권부사채권발행결정"],
    "bw": ["신주인수권부사채권발행결정"],
    "조건부자본증권": ["조건부자본증권발행결정", "자본으로인정되는채무증권발행결정"],
}


@dataclass
class SlotResult:
    intent: str = "unknown"
    # compare 의도의 비교 축. entity=기업 간, period=같은 대상의 기간 간.
    compare_axis: str = ""
    question_type: str = "text"
    calculation: dict[str, Any] = field(default_factory=dict)
    years: list[int] = field(default_factory=list)
    doc_subtype: Optional[str] = None
    base_months: list[int] = field(default_factory=list)
    period_explicit: bool = False
    prefer_latest: bool = False
    relative_terms: list[str] = field(default_factory=list)
    time_mode: str = "fiscal"
    time_mode_explicit: bool = False

    doc_group: Optional[str] = None
    doc_group_candidates: list[str] = field(default_factory=list)
    doc_subtype_candidates: list[str] = field(default_factory=list)
    report_nm_contains: list[str] = field(default_factory=list)

    metric: Optional[str] = None
    metric_confidence: Optional[str] = None
    metric_matches: list[str] = field(default_factory=list)

    basis: Optional[str] = None
    basis_explicit: bool = False
    correction_mode: str = "latest_only"
    notes: list[str] = field(default_factory=list)


def extract(pre: PreprocessResult, entities: EntityResult, index: CorpusIndex) -> SlotResult:
    slots = SlotResult()
    cfg = index.config
    text = pre.text
    sq = pre.squashed

    _extract_years(text, sq, slots, cfg)
    _extract_period(text, sq, slots, cfg)
    _extract_metric(sq, slots, index)
    _extract_doc_keywords(sq, slots, index)
    _extract_basis(sq, slots, cfg)
    _extract_correction(sq, slots, cfg)
    _extract_intent(sq, slots, entities, cfg)
    slots.compare_axis = resolve_compare_axis(slots, entities)
    from .calculation import build_calculation, canonical_question_type
    slots.question_type = canonical_question_type(slots.intent, compare_axis=slots.compare_axis)
    slots.calculation = build_calculation(
        slots.intent,
        text,
        slots.metric,
        metric_matches=slots.metric_matches,
        compare_axis=slots.compare_axis,
    )
    _resolve_time_mode(sq, slots, cfg)
    return slots


# --- 시간 ---------------------------------------------------------------------


def _extract_years(text: str, sq: str, slots: SlotResult, cfg: Any) -> None:
    years: list[int] = []

    for match in _RANGE.finditer(text):
        start, end = int(match.group(1)), int(match.group(2))
        if start <= end and end - start <= 10:
            years.extend(range(start, end + 1))

    for match in _YEAR4.finditer(text):
        years.append(int(match.group(1)))

    for match in _YEAR2.finditer(text):
        years.append(2000 + int(match.group(1)))

    for match in _FY.finditer(text):
        raw = match.group(1)
        years.append(int(raw) if len(raw) == 4 else 2000 + int(raw))

    reference_year = int(str(cfg.defaults.get("reference_date", "2026-03-31"))[:4])
    for term, rule in cfg.defaults.get("relative_time_cues", {}).items():
        if squash(term) not in sq:
            continue
        slots.relative_terms.append(term)
        if "year_offset" in rule:
            years.append(reference_year + int(rule["year_offset"]))
        if rule.get("prefer_latest"):
            slots.prefer_latest = True

    slots.years = sorted(dict.fromkeys(years))


def _extract_period(text: str, sq: str, slots: SlotResult, cfg: Any) -> None:
    quarter_map = cfg.bounds.get("quarter_to_period", {})

    quarter = _QUARTER.search(text) or _QUARTER_Q.search(text) or _QUARTER_ALT.search(text)
    if quarter:
        mapped = quarter_map.get(quarter.group(1))
        if mapped:
            slots.doc_subtype = mapped["doc_subtype"]
            slots.base_months = [int(mapped["base_month"])]
            slots.period_explicit = True
            if quarter.group(1) == "4":
                slots.notes.append("4분기 단독 보고서는 없어 사업보고서(연간)로 해석했습니다.")
            return

    if "상반기" in text or "반기" in text:
        slots.doc_subtype = "half"
        slots.base_months = [6]
        slots.period_explicit = True
        return

    if "하반기" in text:
        slots.doc_subtype = "annual"
        slots.base_months = [12]
        slots.period_explicit = True
        slots.notes.append("하반기 단독 보고서는 없어 사업보고서(연간)로 해석했습니다.")
        return

    if any(token in sq for token in ("연간", "연결연간", "한해")):
        slots.doc_subtype = "annual"
        slots.base_months = [12]
        slots.period_explicit = True


def _resolve_time_mode(sq: str, slots: SlotResult, cfg: Any) -> None:
    disclosure_cues = cfg.defaults.get("time_mode_cues", {}).get("disclosure", [])
    has_cue = any(squash(cue) in sq for cue in disclosure_cues)

    if slots.doc_group in ("major", "exchange", "holding"):
        slots.time_mode = "disclosure"
        slots.time_mode_explicit = True
        return
    if slots.doc_group == "periodic":
        slots.time_mode = "fiscal"
        slots.time_mode_explicit = True
        return
    if has_cue:
        slots.time_mode = "disclosure"
        slots.time_mode_explicit = True
        return
    slots.time_mode = "fiscal"


# --- 지표 · 공시유형 -----------------------------------------------------------


def _extract_metric(sq: str, slots: SlotResult, index: CorpusIndex) -> None:
    best: Optional[tuple[int, dict[str, Any]]] = None
    for metric in index.config.metrics.get("metrics", []):
        for label in metric.get("labels", []):
            key = squash(label)
            if key and key in sq:
                slots.metric_matches.append(metric["key"])
                if best is None or len(key) > best[0]:
                    best = (len(key), metric)
                break

    if best is None:
        return

    metric = best[1]
    slots.metric = metric["key"]
    slots.metric_confidence = metric.get("confidence")
    slots.doc_group = metric.get("doc_group")
    slots.doc_group_candidates = list(metric.get("doc_group_candidates", []))
    if metric.get("doc_subtype"):
        slots.doc_subtype_candidates = [metric["doc_subtype"]]
    elif metric.get("doc_subtype_candidates"):
        slots.doc_subtype_candidates = list(metric["doc_subtype_candidates"])

    if metric["key"] == "fundraising":
        specific: list[str] = []
        for token, report_names in _FUNDRAISING_INSTRUMENTS.items():
            if squash(token) in sq:
                specific.extend(report_names)
        slots.report_nm_contains = sorted(dict.fromkeys(specific)) or list(
            metric.get("report_nm_contains", [])
        )
    else:
        slots.report_nm_contains = list(metric.get("report_nm_contains", []))

    slots.metric_matches = sorted(dict.fromkeys(slots.metric_matches))


def _extract_doc_keywords(sq: str, slots: SlotResult, index: CorpusIndex) -> None:
    """보고서 종류가 질의에 직접 적혀 있으면 지표 추정보다 우선한다."""
    for entry in index.config.metrics.get("doc_keywords", []):
        for label in entry.get("labels", []):
            if squash(label) not in sq:
                continue
            slots.doc_group = entry["doc_group"]
            slots.doc_group_candidates = []
            if entry.get("doc_subtype"):
                if entry["doc_group"] == "periodic":
                    slots.doc_subtype = entry["doc_subtype"]
                    slots.period_explicit = True
                    months = index.config.bounds.get("base_month_by_subtype", {}).get(
                        entry["doc_subtype"]
                    )
                    if isinstance(months, int):
                        slots.base_months = [months]
                    elif isinstance(months, list) and not slots.base_months:
                        slots.base_months = list(months)
                else:
                    slots.doc_subtype_candidates = [entry["doc_subtype"]]
            return


def _extract_basis(sq: str, slots: SlotResult, cfg: Any) -> None:
    for basis, cues in cfg.defaults.get("basis_cues", {}).items():
        for cue in cues:
            if squash(cue) in sq:
                slots.basis = basis
                slots.basis_explicit = True
                return


def _extract_correction(sq: str, slots: SlotResult, cfg: Any) -> None:
    cues = cfg.defaults.get("correction_cues", {}).get("include_chain", [])
    if any(squash(cue) in sq for cue in cues):
        slots.correction_mode = "include_chain"


# --- intent -------------------------------------------------------------------


def _extract_intent(sq: str, slots: SlotResult, entities: EntityResult, cfg: Any) -> None:
    cues = cfg.defaults.get("intent_cues", {})
    order = cues.get("_order", ["exists", "change", "compare", "calc", "list", "lookup"])

    for kind in order:
        for cue in cues.get(kind, []):
            if squash(cue) in sq:
                slots.intent = kind
                return

    if entities.corps or entities.sector:
        slots.intent = "lookup"


def resolve_compare_axis(slots: SlotResult, entities: EntityResult) -> str:
    """비교 대상이 기업인지 기간인지 정한다.

    같은 기업의 두 시점을 비교하는 질의는 순위(rank)가 아니라 증감 계산이라,
    3단계가 기업 비교 경로로 보내지 않도록 여기서 축을 남긴다.
    """
    if slots.intent != "compare":
        return ""
    target_count = len(entities.corps) + (len(entities.sector_members) if entities.sector else 0)
    if target_count >= 2 or entities.sector:
        return "entity"
    if len(slots.years) >= 2:
        return "period"
    return ""
