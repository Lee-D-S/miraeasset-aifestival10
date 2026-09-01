"""1단계 산출물 스키마.

ManifestFilter의 필드명/enum은 manifest.jsonl 컬럼과 동일하게 유지한다.
2단계 검색기는 Intent 전체가 아니라 manifest_filter만 보고 문서를 고를 수 있어야 한다.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Optional

Route = Literal["ok", "need_clarify", "unanswerable", "unsafe"]
IntentKind = Literal["lookup", "calc", "compare", "list", "change", "exists", "unknown"]
TimeMode = Literal["fiscal", "disclosure"]
CorrectionMode = Literal["latest_only", "include_chain", "original_only"]

DOC_GROUPS: tuple[str, ...] = ("periodic", "major", "exchange", "holding")

DOC_SUBTYPES: dict[str, tuple[str, ...]] = {
    "periodic": ("annual", "half", "quarter"),
    # major는 manifest에서 doc_subtype이 전부 None이다. 세부 구분은 report_nm_contains로 한다.
    "major": (),
    "exchange": (
        "단일판매공급계약체결",
        "단일판매공급계약해지",
        "신규시설투자등",
        "투자판단관련주요경영사항",
    ),
    "holding": ("대량보유상황보고서",),
}


@dataclass
class CorpRef:
    """확정된 기업 1건. corp_name이 raw/ 폴더명 및 manifest 조인 키다."""

    corp_name: str
    corp_code: str
    stock_code: str
    listed_name: str
    sector: str
    listing_date: str
    matched_text: str
    match_source: str


@dataclass
class ManifestFilter:
    corp_names: list[str] = field(default_factory=list)
    # "A를 제외한 ..." 질의의 제외 대상. corp_names/sector로 고른 뒤 마지막에 뺀다.
    exclude_corp_names: list[str] = field(default_factory=list)
    sector: Optional[str] = None
    doc_group: Optional[str] = None
    doc_group_candidates: list[str] = field(default_factory=list)
    doc_subtype: Optional[str] = None
    doc_subtype_candidates: list[str] = field(default_factory=list)
    base_years: list[int] = field(default_factory=list)
    base_months: list[int] = field(default_factory=list)
    rcept_from: Optional[str] = None
    rcept_to: Optional[str] = None
    # None = 정정 여부 무관(정정 이력 질의). False = 원본만.
    is_correction: Optional[bool] = False
    report_nm_contains: list[str] = field(default_factory=list)

    def effective_doc_groups(self) -> list[str]:
        if self.doc_group:
            return [self.doc_group]
        return list(self.doc_group_candidates)

    def effective_doc_subtypes(self) -> list[str]:
        if self.doc_subtype:
            return [self.doc_subtype]
        return list(self.doc_subtype_candidates)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TimeSpec:
    mode: TimeMode = "fiscal"
    years: list[int] = field(default_factory=list)
    doc_subtype: Optional[str] = None
    base_months: list[int] = field(default_factory=list)
    prefer_latest: bool = False
    relative_terms: list[str] = field(default_factory=list)
    out_of_range_years: list[int] = field(default_factory=list)


@dataclass
class Intent:
    raw_question: str
    normalized_question: str

    intent: IntentKind = "unknown"
    route: Route = "ok"
    # Canonical question type consumed by the shared Stage3 contract.
    question_type: str = "text"
    calculation: dict[str, Any] = field(default_factory=dict)

    corps: list[CorpRef] = field(default_factory=list)
    # 질의가 명시적으로 제외한 기업. corps/sector_members에서는 이미 빠져 있다.
    excluded_corps: list[CorpRef] = field(default_factory=list)
    sector: Optional[str] = None
    sector_members: list[str] = field(default_factory=list)
    ambiguous_mentions: list[dict[str, Any]] = field(default_factory=list)
    unknown_entities: list[str] = field(default_factory=list)
    related_entities: list[str] = field(default_factory=list)

    metric: Optional[str] = None
    metric_confidence: Optional[str] = None
    basis: Optional[str] = None

    time: TimeSpec = field(default_factory=TimeSpec)
    correction_mode: CorrectionMode = "latest_only"
    allow_pdf_html: bool = True

    manifest_filter: ManifestFilter = field(default_factory=ManifestFilter)
    doc_count: Optional[int] = None
    availability: str = "unknown"

    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    missing_slots: list[str] = field(default_factory=list)
    reject_reason: Optional[str] = None
    clarify_message: Optional[str] = None
    llm_used: bool = False
    # trace_summary()와 같은 문자열. state에는 Intent dict만 실려서 3·4단계가
    # 메서드를 호출할 수 없으므로 직렬화된 값으로도 남긴다.
    think_trace: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def trace_summary(self) -> str:
        """think_trace에 넣을 1단계 요약."""
        parts = [f"intent={self.intent}", f"route={self.route}"]
        if self.corps:
            parts.append("기업=" + ",".join(c.corp_name for c in self.corps))
        if self.excluded_corps:
            parts.append("제외=" + ",".join(c.corp_name for c in self.excluded_corps))
        if self.sector:
            parts.append(f"섹터={self.sector}")
        if self.time.years:
            parts.append("연도=" + ",".join(str(y) for y in self.time.years))
        if self.manifest_filter.doc_group:
            parts.append(f"공시={self.manifest_filter.doc_group}")
        elif self.manifest_filter.doc_group_candidates:
            parts.append("공시후보=" + ",".join(self.manifest_filter.doc_group_candidates))
        if self.metric:
            parts.append(f"지표={self.metric}")
        if self.basis:
            parts.append(f"기준={self.basis}")
        if self.doc_count is not None:
            parts.append(f"후보문서={self.doc_count}건")
        if self.assumptions:
            parts.append("가정=" + " / ".join(self.assumptions))
        return " | ".join(parts)


INTENT_REQUIRED_SLOTS: dict[str, tuple[str, ...]] = {
    "lookup": ("corp_or_sector", "metric"),
    "calc": ("corp_or_sector", "metric", "two_periods"),
    "compare": ("two_targets", "metric"),
    "list": ("corp_or_sector", "time"),
    "change": ("corp_or_sector", "two_periods"),
    "exists": ("corp_or_sector",),
    "unknown": ("corp_or_sector",),
}
