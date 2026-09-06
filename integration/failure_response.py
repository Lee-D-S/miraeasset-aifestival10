"""Deterministic, user-facing explanations for blocked RAG answers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


MAX_FAILURE_ANSWER_CHARS = 300


class FailureReasonCode(str, Enum):
    """Stable internal categories for a non-success public answer."""

    UNSAFE_REQUEST = "UNSAFE_REQUEST"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    MISSING_ENTITY = "MISSING_ENTITY"
    AMBIGUOUS_ENTITY = "AMBIGUOUS_ENTITY"
    MISSING_REQUIRED_SLOT = "MISSING_REQUIRED_SLOT"
    OUT_OF_CORPUS_ENTITY = "OUT_OF_CORPUS_ENTITY"
    OUT_OF_CORPUS_PERIOD = "OUT_OF_CORPUS_PERIOD"
    NO_RETRIEVAL_EVIDENCE = "NO_RETRIEVAL_EVIDENCE"
    INSUFFICIENT_FACT_EVIDENCE = "INSUFFICIENT_FACT_EVIDENCE"
    VALIDATION_FAILED = "VALIDATION_FAILED"


@dataclass(frozen=True)
class FailureResponse:
    """A public answer plus its non-public diagnostic category."""

    reason_code: FailureReasonCode
    legacy_conclusion: str
    public_reason: str
    guidance: str | None = None

    @property
    def answer(self) -> str:
        reason = _clean_value(self.public_reason, limit=180)
        guidance = _clean_value(self.guidance or "", limit=100)
        parts = [self.legacy_conclusion.strip()]
        if reason:
            parts.append(f"이유: {reason}")
        if guidance:
            parts.append(f"다시 질문하려면: {guidance}")
        rendered = "\n\n".join(parts)
        if len(rendered) <= MAX_FAILURE_ANSWER_CHARS:
            return rendered
        return _fit_to_limit(self.legacy_conclusion, self.public_reason, self.guidance)


_CONCLUSIONS = {
    "need_clarify": "질문의 기업·기간·기준이 명확하지 않습니다.",
    "unanswerable": "제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.",
    "unsafe": "공시 근거만으로 답변할 수 없는 요청입니다.",
    "no_evidence": "제공된 공시에서 질문에 필요한 근거를 확인할 수 없습니다.",
    "validation": "제공된 공시 근거만으로 답변을 검증할 수 없습니다.",
}

_KNOWN_LEGACY_CONCLUSIONS = frozenset({
    "제공된 공시에서 질문에 필요한 근거를 확인할 수 없습니다.",
    "계산에 필요한 기간·단위·기준을 공시 근거에서 확인할 수 없습니다.",
    "비교에 필요한 동일 기간·단위·기준의 공시 근거를 확인할 수 없습니다.",
    "제공된 공시 근거만으로 답변을 검증할 수 없습니다.",
})

_MISSING_SLOT_LABELS = {
    "corp_or_sector": "기업명 또는 섹터",
    "two_targets": "비교 대상",
    "two_periods": "비교 기간",
    "time": "기간",
    "metric": "확인할 항목",
}

_METRIC_LABELS = {
    "revenue": "매출액",
    "operating_profit": "영업이익",
    "net_income": "당기순이익",
    "total_assets": "자산총계",
    "capex": "설비투자",
    "supply_contract": "공급계약",
}


def _clean_value(value: Any, *, limit: int = 48) -> str:
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return ""
    return text[:limit].rstrip()


def _items(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _item_label(value: Any) -> str:
    if isinstance(value, Mapping):
        for key in ("corp_name", "name", "token", "label", "value"):
            candidate = _clean_value(value.get(key, ""))
            if candidate:
                return candidate
        return ""
    return _clean_value(value)


def _first_item_label(value: Any) -> str:
    for item in _items(value):
        label = _item_label(item)
        if label:
            return label
    return ""


def _intent(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("intent")
    return value if isinstance(value, Mapping) else {}


def _reasoner_result(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("reasoner_result")
    return value if isinstance(value, Mapping) else {}


def _retriever_result(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("retriever_result")
    return value if isinstance(value, Mapping) else {}


def _reject_reason(intent: Mapping[str, Any]) -> str:
    return str(intent.get("reject_reason") or "").strip().lower()


def _years(intent: Mapping[str, Any]) -> list[str]:
    time = intent.get("time")
    if not isinstance(time, Mapping):
        time = {}
    values = time.get("out_of_range_years") or time.get("years") or []
    result: list[str] = []
    for value in _items(values):
        text = _clean_value(value, limit=12)
        if text and text not in result:
            result.append(text)
    return result[:2]


def _metric_label(intent: Mapping[str, Any]) -> str:
    metric = _clean_value(intent.get("metric", ""))
    return _METRIC_LABELS.get(metric, metric)


def _legacy_conclusion(state: Mapping[str, Any], *, default: str) -> str:
    candidates = [state.get("answer"), _reasoner_result(state).get("answer")]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text in _KNOWN_LEGACY_CONCLUSIONS:
            return text
    return default


def _missing_slot_text(intent: Mapping[str, Any]) -> str:
    labels = [
        _MISSING_SLOT_LABELS.get(str(item), "필수 정보")
        for item in _items(intent.get("missing_slots"))
    ]
    labels = list(dict.fromkeys(label for label in labels if label))
    if not labels:
        return "기업명·기간·확인할 항목"
    return "·".join(labels[:3])


def _has_documents(state: Mapping[str, Any]) -> bool:
    retriever = _retriever_result(state)
    for key in ("cited_documents", "documents"):
        value = retriever.get(key)
        if isinstance(value, list) and value:
            return True
    reasoner = _reasoner_result(state)
    citations = reasoner.get("citations")
    return isinstance(citations, list) and bool(citations)


def _failed_calculation_or_comparison(reasoner: Mapping[str, Any]) -> tuple[str, str] | None:
    calculations = reasoner.get("calculations")
    if isinstance(calculations, list) and calculations and not any(
        isinstance(item, Mapping) and item.get("status") == "ok" for item in calculations
    ):
        return (
            "계산에 필요한 기간·단위·기준을 공시 근거에서 확인하지 못했습니다.",
            "연도와 연결·별도 기준을 함께 포함해 질문해 주세요.",
        )
    comparisons = reasoner.get("comparison_results")
    if isinstance(comparisons, list) and comparisons and not any(
        isinstance(item, Mapping) and item.get("status") == "ok" for item in comparisons
    ):
        return (
            "비교 대상의 동일 기간·단위·기준을 공시 근거에서 확인하지 못했습니다.",
            "비교할 기업과 동일한 기준 기간을 함께 지정해 주세요.",
        )
    return None


def _validation_reason(
    *,
    numeric_check: Mapping[str, Any],
    citation_check: Mapping[str, Any],
    semantic_check: Mapping[str, Any],
    reason_hint: str | None,
) -> str:
    if reason_hint:
        return _clean_value(reason_hint, limit=180)
    if numeric_check and numeric_check.get("pass") is False:
        return "답변의 수치가 공시 근거와 일치하는지 확인하지 못했습니다."
    if citation_check and citation_check.get("pass") is False:
        return "답변에 사용한 출처를 공시 근거와 연결하지 못했습니다."
    if semantic_check and (
        semantic_check.get("unsupported_claims") or semantic_check.get("missing_aspects")
    ):
        return "답변이 질문의 요구사항과 공시 근거를 충분히 충족하는지 확인하지 못했습니다."
    return "답변의 수치·출처·요청 조건이 공시 근거와 일치하는지 확인하지 못했습니다."


def _fit_to_limit(conclusion: str, reason: str, guidance: str | None) -> str:
    """Keep the conclusion and shorten only dynamic explanatory text."""

    conclusion = conclusion.strip()
    reason = _clean_value(reason, limit=150)
    guidance = _clean_value(guidance or "", limit=100)
    parts = [conclusion, f"이유: {reason}"]
    if guidance:
        parts.append(f"다시 질문하려면: {guidance}")
    rendered = "\n\n".join(parts)
    if len(rendered) <= MAX_FAILURE_ANSWER_CHARS:
        return rendered
    if guidance:
        remaining = MAX_FAILURE_ANSWER_CHARS - len("\n\n".join(parts[:2])) - len("\n\n다시 질문하려면: ")
        guidance = guidance[: max(0, remaining)].rstrip()
        parts[2] = f"다시 질문하려면: {guidance}"
        rendered = "\n\n".join(parts)
    if len(rendered) <= MAX_FAILURE_ANSWER_CHARS:
        return rendered
    remaining = MAX_FAILURE_ANSWER_CHARS - len(conclusion) - len("\n\n이유: ")
    return f"{conclusion}\n\n이유: {reason[:max(0, remaining)].rstrip()}"


def build_failure_response(
    state: Mapping[str, Any],
    *,
    numeric_check: Mapping[str, Any] | None = None,
    citation_check: Mapping[str, Any] | None = None,
    semantic_check: Mapping[str, Any] | None = None,
    reason_hint: str | None = None,
) -> FailureResponse:
    """Build a safe public explanation from structured pipeline state."""

    intent = _intent(state)
    route = str(state.get("route") or intent.get("route") or "unanswerable")
    reject_reason = _reject_reason(intent)
    reasoner = _reasoner_result(state)
    validator = state.get("validator_result")
    validator = validator if isinstance(validator, Mapping) else {}
    numeric = numeric_check if numeric_check is not None else validator.get("numeric_check", {})
    citation = citation_check if citation_check is not None else validator.get("citation_check", {})
    semantic = semantic_check if semantic_check is not None else validator.get("semantic_check", {})
    numeric = numeric if isinstance(numeric, Mapping) else {}
    citation = citation if isinstance(citation, Mapping) else {}
    semantic = semantic if isinstance(semantic, Mapping) else {}

    if route == "unsafe":
        return FailureResponse(
            FailureReasonCode.UNSAFE_REQUEST,
            _CONCLUSIONS["unsafe"],
            "요청이 제공된 공시 기반 사실 확인 범위를 벗어납니다.",
        )

    if reject_reason.startswith("out_of_scope:"):
        return FailureResponse(
            FailureReasonCode.OUT_OF_SCOPE,
            _CONCLUSIONS["unanswerable"],
            "요청한 정보가 제공된 공시 기반 Agent의 처리 범위에 포함되지 않습니다.",
        )

    if reject_reason == "ambiguous_corp" or _items(intent.get("ambiguous_mentions")):
        token = _first_item_label(intent.get("ambiguous_mentions"))
        detail = f"‘{token}’만으로는 기업을 특정할 수 없습니다." if token else "질문의 기업명이 여러 대상과 일치해 기업을 특정할 수 없습니다."
        return FailureResponse(
            FailureReasonCode.AMBIGUOUS_ENTITY,
            _CONCLUSIONS["need_clarify"],
            detail,
            "공식 기업명 또는 종목명을 포함해 질문해 주세요.",
        )

    if reject_reason == "missing_corp" or "corp_or_sector" in _items(intent.get("missing_slots")):
        return FailureResponse(
            FailureReasonCode.MISSING_ENTITY,
            _CONCLUSIONS["need_clarify"],
            "질문에 기업명 또는 섹터가 없어 조회 대상을 특정할 수 없습니다.",
            "기업명 또는 섹터를 포함해 질문해 주세요.",
        )

    if _items(intent.get("missing_slots")):
        return FailureResponse(
            FailureReasonCode.MISSING_REQUIRED_SLOT,
            _CONCLUSIONS["need_clarify"],
            f"질문에 {_missing_slot_text(intent)} 정보가 부족합니다.",
            "기업명·기간·확인할 항목을 구체적으로 포함해 질문해 주세요.",
        )

    if route == "need_clarify":
        return FailureResponse(
            FailureReasonCode.MISSING_REQUIRED_SLOT,
            _CONCLUSIONS["need_clarify"],
            "기업명·기간·확인할 항목 중 필요한 정보가 부족합니다.",
            "기업명·기간·확인할 항목을 구체적으로 포함해 질문해 주세요.",
        )

    if reject_reason == "out_of_universe_corp":
        name = _first_item_label(intent.get("unknown_entities"))
        subject = f"‘{name}’은(는)" if name else "요청한 기업은"
        return FailureResponse(
            FailureReasonCode.OUT_OF_CORPUS_ENTITY,
            _CONCLUSIONS["unanswerable"],
            f"{subject} 제공된 공시 코퍼스 70개사에 포함되지 않습니다.",
            "제공된 코퍼스에 포함된 기업을 기준으로 질문해 주세요.",
        )

    if reject_reason in {"out_of_corpus_year", "out_of_corpus_period"}:
        years = _years(intent)
        year_text = f"요청하신 {', '.join(years)}년은" if years else "요청하신 기간은"
        return FailureResponse(
            FailureReasonCode.OUT_OF_CORPUS_PERIOD,
            _CONCLUSIONS["unanswerable"],
            f"{year_text} 제공된 공시 범위(2023년~2026년 1분기) 밖입니다.",
            "2023년부터 2026년 1분기 사이의 기간을 기준으로 질문해 주세요.",
        )

    if reason_hint is not None:
        return FailureResponse(
            FailureReasonCode.VALIDATION_FAILED,
            _legacy_conclusion(state, default=_CONCLUSIONS["validation"]),
            reason_hint,
        )

    if route == "unanswerable":
        return FailureResponse(
            FailureReasonCode.NO_RETRIEVAL_EVIDENCE,
            _CONCLUSIONS["unanswerable"],
            "제공된 공시 코퍼스에서 질문에 필요한 근거를 확인하지 못했습니다.",
            "기업명·기간·공시 유형·지표를 구체적으로 포함해 질문해 주세요.",
        )

    failed_analysis = _failed_calculation_or_comparison(reasoner)
    if failed_analysis is not None:
        reason, guidance = failed_analysis
        return FailureResponse(
            FailureReasonCode.INSUFFICIENT_FACT_EVIDENCE,
            _legacy_conclusion(state, default=_CONCLUSIONS["no_evidence"]),
            reason,
            guidance,
        )

    if not _has_documents(state) and (
        not reasoner or not reasoner.get("facts")
    ):
        metric = _metric_label(intent)
        detail = "요청한 기업·기간·지표 조건에 맞는 근거 문서를 검색하지 못했습니다."
        if metric:
            detail = f"요청한 {metric}에 맞는 근거 문서를 검색하지 못했습니다."
        return FailureResponse(
            FailureReasonCode.NO_RETRIEVAL_EVIDENCE,
            _legacy_conclusion(state, default=_CONCLUSIONS["no_evidence"]),
            detail,
            "기업·기간·공시 유형·지표를 구체적으로 포함해 질문해 주세요.",
        )

    if validator.get("status") == "validation_failed" or (
        numeric and numeric.get("pass") is False
    ) or (citation and citation.get("pass") is False) or (semantic and semantic.get("pass") is False):
        return FailureResponse(
            FailureReasonCode.VALIDATION_FAILED,
            _legacy_conclusion(state, default=_CONCLUSIONS["validation"]),
            _validation_reason(
                numeric_check=numeric,
                citation_check=citation,
                semantic_check=semantic,
                reason_hint=reason_hint,
            ),
        )

    return FailureResponse(
        FailureReasonCode.INSUFFICIENT_FACT_EVIDENCE,
        _legacy_conclusion(state, default=_CONCLUSIONS["no_evidence"]),
        "검색된 공시에서 질문에 필요한 지표·기간·단위·기준의 Fact를 충분히 확인하지 못했습니다.",
        "기업·기간·지표와 연결 또는 별도 기준을 함께 포함해 질문해 주세요.",
    )


__all__ = [
    "FailureReasonCode",
    "FailureResponse",
    "MAX_FAILURE_ANSWER_CHARS",
    "build_failure_response",
]
