"""Stage1 metric registry used by Stage3 Fact extraction.

The registry maps names emitted by Stage1 to evidence labels. It never
reclassifies a question or changes the metric in the incoming Intent.
"""

from __future__ import annotations

from typing import Any

METRIC_SPECS: dict[str, dict[str, Any]] = {
    "revenue": {"numeric_labels": ("매출액", "매출")},
    "operating_profit": {"numeric_labels": ("영업이익", "영업손익")},
    "net_income": {"numeric_labels": ("당기순이익", "순이익")},
    "total_assets": {
        "numeric_labels": (
            "자산총계",
            "총자산",
            "부채총계",
            "총부채",
            "자본총계",
            "총자본",
            "부채비율",
            "부채 비율",
            "자기자본비율",
            "자기자본 비율",
        )
    },
    "capex": {"numeric_labels": ("설비투자", "시설투자", "신규시설투자")},
    "supply_contract": {
        "numeric_labels": ("계약금액", "계약금액(원)", "최근 매출액", "최근매출액", "매출액 대비", "매출액대비"),
        "field_labels": {
            "contract_name": ("체결계약명", "계약명"),
            "counterparty": ("계약상대방", "계약 상대방"),
            "region": ("판매·공급지역", "판매/공급지역", "판매 공급지역"),
            "contract_period": ("계약기간",),
            "contract_start": ("계약시작일", "계약 시작일"),
            "contract_end": ("계약종료일", "계약 종료일"),
            "contract_date": ("계약일자", "계약 체결일"),
            "terms": ("주요 계약조건", "주요계약조건"),
            "other_notes": ("기타 투자판단에 참고할 사항", "기타 중요사항"),
        },
    },
    "contract_termination": {
        "numeric_labels": ("기존 계약금액", "기존계약금액", "해지금액", "해지 금액", "계약금액"),
        "field_labels": {
            "original_contract": ("원계약", "해지 대상 계약"),
            "counterparty": ("계약상대방", "계약 상대방"),
            "termination_date": ("해지일", "계약 해지일"),
            "reason": ("해지사유", "해지 사유"),
            "remaining_obligation": ("잔여 의무", "잔여의무"),
            "business_impact": ("사업 영향", "영업에 미치는 영향"),
        },
    },
    "facility_investment": {
        "numeric_labels": ("투자금액", "투자 금액", "총투자금액", "총 투자금액", "최근 매출액"),
        "field_labels": {
            "facility": ("투자대상", "시설명", "시설"),
            "location": ("투자지역", "소재지", "위치"),
            "purpose": ("투자목적", "투자 목적"),
            "funding_source": ("자금조달", "재원"),
            "investment_period": ("투자기간", "투자 기간"),
            "completion_date": ("완공예정일", "완공 예정일"),
            "business_effect": ("생산·사업 효과", "생산능력", "기대효과"),
        },
    },
    "mgmt_judgement": {
        "section_labels": ("투자판단 관련 주요경영사항", "주요경영사항"),
    },
    "fundraising": {
        "numeric_labels": ("발행금액", "모집금액", "조달금액", "발행가액", "주식수", "주식 수", "신주수", "신주 수"),
        "field_labels": {
            "issuance_type": ("발행 유형", "증권의 종류", "증권종류"),
            "use_of_funds": ("자금 사용 목적", "자금의 사용목적", "자금 용도"),
            "allottee": ("배정 대상", "인수인", "배정대상"),
            "maturity": ("만기", "상환기일"),
            "interest_rate": ("표면이자율", "금리"),
            "schedule": ("청약일", "납입일", "청약·납입 일정"),
        },
    },
    "treasury_stock": {"section_labels": ("자기주식", "자사주", "신탁계약")},
    "restructuring": {"section_labels": ("회사분할", "회사합병", "영업양수", "영업정지")},
    "major_shareholding": {
        "numeric_labels": ("보유주식수", "보유주식 수", "보유주식 등의 수", "보유비율", "보유 비율", "지분율", "변동주식수", "변동 주식 수"),
        "field_labels": {
            "reporter": ("보고자",),
            "special_related_party": ("특별관계자",),
            "issuer": ("발행회사",),
            "purpose": ("보유목적", "보유 목적"),
            "change_reason": ("변동 사유", "변동사유"),
            "contract_or_collateral": ("계약·담보", "계약", "담보계약"),
            "funding_source": ("취득자금 원천", "자금 원천"),
        },
    },
    "business_overview": {"section_labels": ("사업의 내용", "사업 개요", "주요 제품·서비스", "제품·서비스")},
    "investment_plan": {"section_labels": ("투자계획", "투자 계획", "투자 목적", "자금 사용 목적")},
    "rnd": {
        "numeric_labels": ("연구개발비", "연구개발비용", "경상연구개발비"),
        "section_labels": ("연구개발", "R&D", "연구 개발"),
    },
    "dividend": {"section_labels": ("배당", "배당에 관한 사항")},
    "employees": {"section_labels": ("임원 및 직원", "임직원", "직원")},
    "shareholders": {"section_labels": ("주주", "주식의 총수", "주주에 관한 사항")},
    "litigation": {"section_labels": ("소송", "소송 등의 제기", "법적 분쟁")},
}


STAGE1_METRICS = frozenset(METRIC_SPECS)
TEXT_METRICS = frozenset(metric for metric, spec in METRIC_SPECS.items() if spec.get("section_labels"))

# Stage1 uses total_assets as the retrieval metric for the financial position
# group. Stage3 separates the actual output facts by the label found in the
# evidence.
FACT_METRIC_BY_LABEL = {
    "자산총계": "assets",
    "총자산": "assets",
    "부채총계": "liabilities",
    "총부채": "liabilities",
    "자본총계": "equity",
    "총자본": "equity",
    "부채비율": "ratio",
    "부채 비율": "ratio",
    "자기자본비율": "ratio",
    "자기자본 비율": "ratio",
}


def is_stage1_metric(metric: str | None) -> bool:
    return metric in STAGE1_METRICS


def numeric_labels_for(metric: str | None) -> tuple[str, ...]:
    if metric in METRIC_SPECS:
        return tuple(METRIC_SPECS[metric].get("numeric_labels", ()))
    labels: list[str] = []
    for spec in METRIC_SPECS.values():
        labels.extend(spec.get("numeric_labels", ()))
    return tuple(dict.fromkeys(labels))


def field_labels_for(metric: str | None) -> dict[str, tuple[str, ...]]:
    spec = METRIC_SPECS.get(metric or "", {})
    return dict(spec.get("field_labels", {}))


def section_labels_for(metric: str | None) -> tuple[str, ...]:
    spec = METRIC_SPECS.get(metric or "", {})
    return tuple(spec.get("section_labels", ()))


def fact_metric_for_label(label: str, requested_metric: str | None) -> str:
    compact = "".join(str(label or "").split())
    if compact in FACT_METRIC_BY_LABEL:
        return FACT_METRIC_BY_LABEL[compact]
    for key, metric in FACT_METRIC_BY_LABEL.items():
        if key and key in compact:
            return metric
    return requested_metric or "unknown"


__all__ = [
    "FACT_METRIC_BY_LABEL",
    "METRIC_SPECS",
    "STAGE1_METRICS",
    "TEXT_METRICS",
    "fact_metric_for_label",
    "field_labels_for",
    "is_stage1_metric",
    "numeric_labels_for",
    "section_labels_for",
]
