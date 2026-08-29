"""④-1 기본값 적용과 ManifestFilter 생성.

코퍼스 경계를 여기서 반영한다. 정기공시는 FY2023~FY2025 전체 + FY2026 1분기만 있다.
- 기간이 질의에 없어 기본값으로 채운 경우: 가능한 기간으로 폴백하고 assumptions에 남긴다.
- 기간이 질의에 명시됐는데 코퍼스에 없는 경우: 폴백하지 않고 router가 unanswerable로 판단한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from ..index.corpus_index import CorpusIndex
from ..models.intent import ManifestFilter
from .entity_linker import EntityResult
from .slot_extractor import SlotResult


@dataclass
class BuildResult:
    manifest_filter: ManifestFilter
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    unavailable_periods: list[str] = field(default_factory=list)
    out_of_range_years: list[int] = field(default_factory=list)
    in_range_years: list[int] = field(default_factory=list)
    availability: str = "unknown"


def build(
    entities: EntityResult,
    slots: SlotResult,
    index: CorpusIndex,
) -> BuildResult:
    cfg = index.config
    defaults = cfg.defaults.get("defaults", {})
    fiscal = cfg.bounds.get("fiscal", {})
    available: dict[str, list[int]] = {
        str(year): list(months) for year, months in fiscal.get("available_periods", {}).items()
    }
    min_year = int(fiscal.get("min_year", 2023))
    max_year = int(fiscal.get("max_year", 2026))

    result = BuildResult(manifest_filter=ManifestFilter())
    flt = result.manifest_filter

    flt.corp_names = [c.corp_name for c in entities.corps]
    # 기업이 확정되면 섹터는 참고 정보일 뿐이라 필터에서 뺀다.
    flt.sector = None if flt.corp_names else entities.sector

    flt.doc_group = slots.doc_group
    flt.doc_group_candidates = list(slots.doc_group_candidates)
    flt.report_nm_contains = list(slots.report_nm_contains)

    if slots.doc_group == "periodic" or (not slots.doc_group and slots.time_mode == "fiscal"):
        _apply_periodic_period(slots, flt, result, defaults, available)

    for subtype in slots.doc_subtype_candidates:
        if subtype != flt.doc_subtype and subtype not in flt.doc_subtype_candidates:
            flt.doc_subtype_candidates.append(subtype)

    # doc_group이 여러 후보면 doc_subtype을 한쪽으로 고정하지 않는다.
    # 고정하면 다른 그룹(예: exchange/신규시설투자등)이 전부 걸러진다.
    if not flt.doc_group and flt.doc_group_candidates and flt.doc_subtype:
        if flt.doc_subtype not in flt.doc_subtype_candidates:
            flt.doc_subtype_candidates.insert(0, flt.doc_subtype)
        flt.doc_subtype = None

    result.out_of_range_years = [y for y in slots.years if not min_year <= y <= max_year]
    result.in_range_years = [y for y in slots.years if min_year <= y <= max_year]

    if slots.time_mode == "fiscal":
        flt.base_years = list(result.in_range_years)
        _check_fiscal_availability(flt, result, available)
        # 그룹 후보에 수시공시가 섞여 있으면 그쪽은 접수일로 잘라야 한다.
        if any(group != "periodic" for group in flt.effective_doc_groups()):
            _apply_disclosure_window(slots, flt, result, cfg)
    else:
        _apply_disclosure_window(slots, flt, result, cfg)

    if slots.correction_mode == "include_chain":
        flt.is_correction = None
        result.assumptions.append("정정 이력 질의로 보고 원본과 정정본을 함께 조회합니다.")
    else:
        flt.is_correction = bool(defaults.get("is_correction", False))

    _apply_basis(slots, flt, result, defaults)
    _check_listing_dates(entities, flt, result, index)
    _check_known_empty(flt, result, index)

    result.assumptions.extend(slots.notes)
    return result


def _apply_periodic_period(
    slots: SlotResult,
    flt: ManifestFilter,
    result: BuildResult,
    defaults: dict[str, Any],
    available: dict[str, list[int]],
) -> None:
    if slots.doc_subtype:
        flt.doc_subtype = slots.doc_subtype
        flt.base_months = list(slots.base_months)
        return

    if slots.prefer_latest and not slots.years:
        result.assumptions.append("기간이 명시되지 않아 가장 최근 보고서를 우선합니다.")
        return

    if not slots.years:
        return

    fallback = str(defaults.get("period_when_year_only", "annual"))
    flt.doc_subtype = fallback
    month_map = {"annual": [12], "half": [6], "quarter": [3, 9]}
    flt.base_months = month_map.get(fallback, [])
    result.assumptions.append(f"보고서 종류가 명시되지 않아 {_subtype_label(fallback)}를 기준으로 봅니다.")

    # 기본값으로 채운 기간이 그 연도에 없으면(예: FY2026 사업보고서) 가능한 기간으로 옮긴다.
    unavailable = [
        year
        for year in slots.years
        if str(year) in available and not set(flt.base_months) & set(available[str(year)])
    ]
    if unavailable and len(unavailable) == len(
        [y for y in slots.years if str(y) in available]
    ):
        target_year = str(max(unavailable))
        months = available.get(target_year, [])
        if months:
            month = min(months)
            flt.doc_subtype = _subtype_for_month(month)
            flt.base_months = [month]
            result.assumptions.append(
                f"{target_year}년은 코퍼스에 {_subtype_label(flt.doc_subtype)}까지만 있어 해당 보고서로 조회합니다."
            )


def _check_fiscal_availability(
    flt: ManifestFilter,
    result: BuildResult,
    available: dict[str, list[int]],
) -> None:
    if not flt.base_years or not flt.base_months:
        return
    for year in flt.base_years:
        months = available.get(str(year))
        if months is None:
            continue
        if not set(flt.base_months) & set(months):
            result.unavailable_periods.append(f"{year}년 {_subtype_label(flt.doc_subtype)}")


def _apply_disclosure_window(
    slots: SlotResult,
    flt: ManifestFilter,
    result: BuildResult,
    cfg: Any,
) -> None:
    window = cfg.bounds.get("disclosure", {})
    event_from = str(window.get("event_from", "20230101"))
    event_to = str(window.get("event_to", "20260331"))

    if not result.in_range_years:
        if slots.prefer_latest:
            result.assumptions.append("기간이 명시되지 않아 최근 공시를 우선합니다.")
        return

    lo = f"{min(result.in_range_years)}0101"
    hi = f"{max(result.in_range_years)}1231"
    flt.rcept_from = max(lo, event_from)
    flt.rcept_to = min(hi, event_to)

    if hi > event_to:
        result.warnings.append(
            f"수시공시 수집 범위는 {event_from}~{event_to}입니다. 요청 기간 중 {event_to} 이후는 조회할 수 없습니다."
        )


def _apply_basis(
    slots: SlotResult,
    flt: ManifestFilter,
    result: BuildResult,
    defaults: dict[str, Any],
) -> None:
    financial = slots.metric in {
        "revenue",
        "operating_profit",
        "net_income",
        "total_assets",
        "capex",
        "rnd",
    }
    if slots.basis_explicit or not financial:
        return
    default_basis = defaults.get("basis")
    if default_basis:
        slots.basis = default_basis
        result.assumptions.append(f"연결/별도 기준이 명시되지 않아 {default_basis}기준으로 봅니다.")


def _check_listing_dates(
    entities: EntityResult,
    flt: ManifestFilter,
    result: BuildResult,
    index: CorpusIndex,
) -> None:
    if not flt.base_years:
        return
    for corp in entities.corps:
        if not corp.listing_date:
            continue
        listing_year = int(corp.listing_date[:4])
        earlier = [year for year in flt.base_years if year < listing_year]
        if earlier:
            result.warnings.append(
                f"{corp.corp_name}은 {corp.listing_date} 상장으로 "
                f"{', '.join(str(y) for y in earlier)}년 정기공시가 없을 수 있습니다."
            )


def _check_known_empty(flt: ManifestFilter, result: BuildResult, index: CorpusIndex) -> None:
    known_empty = set(index.config.metrics.get("known_empty_report_nm", []))
    if flt.report_nm_contains and set(flt.report_nm_contains) <= known_empty:
        result.warnings.append(
            "해당 공시 유형은 코퍼스에 0건입니다(건수 0 ≠ 결측). 2단계에서 '확인되지 않음'으로 처리해야 합니다."
        )


def _subtype_label(subtype: Optional[str]) -> str:
    return {"annual": "사업보고서", "half": "반기보고서", "quarter": "분기보고서"}.get(
        subtype or "", "정기보고서"
    )


def _subtype_for_month(month: int) -> str:
    return {12: "annual", 6: "half", 3: "quarter", 9: "quarter"}.get(month, "quarter")
