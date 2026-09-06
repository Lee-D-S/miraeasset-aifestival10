"""Interpreter calculation-plan extraction for the shared Reasoner contract."""

from __future__ import annotations

from typing import Any

from ..index.corpus_index import squash


SUPPORTED_OPERATIONS = frozenset({
    "add", "subtract", "multiply", "divide", "percentage_change", "cagr",
    "ratio_percent", "margin", "sum", "average", "min", "max", "rank",
})

# 3단계 metric_registry에서 numeric_labels를 가진 재무 지표. 이 지표들만 추이를
# 수치로 계산할 수 있다. rnd·dividend·employees는 3단계가 서술 구간으로만 다룬다.
NUMERIC_METRICS = frozenset({
    "revenue", "operating_profit", "net_income", "total_assets", "capex",
})

# 서로 다른 두 시점의 값이 있어야 성립하는 연산.
TWO_PERIOD_OPERATIONS = frozenset({"percentage_change", "cagr"})
# 값 2개가 필요하지만 두 시점이든 두 대상이든 무관한 연산.
TWO_OPERAND_OPERATIONS = frozenset({"add", "subtract", "multiply", "divide"})

QUESTION_TYPES = {
    "lookup": "lookup",
    "calc": "calculation",
    "compare": "compare",
    "list": "text",
    "change": "event",
    "exists": "event",
    "unknown": "text",
}


def canonical_question_type(
    intent: str,
    *,
    compare_axis: str = "",
    operation: str | None = None,
    metric: str | None = None,
) -> str:
    # 같은 대상의 기간 비교는 순위 매기기가 아니라 증감 계산이다.
    if intent == "compare" and compare_axis == "period":
        return "calculation"
    # 수치 지표의 추이는 공시 이벤트 연결이 아니라 증감 계산으로 답해야 한다.
    if intent == "change" and metric in NUMERIC_METRICS:
        return "calculation"
    question_type = QUESTION_TYPES.get(intent, "text")
    # 연산이 정해졌는데 조회로 표시하면 3단계가 계산 에이전트를 붙이지 않는다.
    if operation and question_type in ("lookup", "text"):
        return "calculation"
    return question_type


def _has(text: str, *cues: str) -> bool:
    return any(squash(cue) in text for cue in cues)


def operation_for(
    question: str, *, intent: str, metric: str | None, compare_axis: str = ""
) -> str | None:
    """Map explicit Korean calculation cues to Reasoner's whitelist."""

    text = squash(question)
    if intent == "compare":
        return "percentage_change" if compare_axis == "period" else "rank"
    if intent == "change" and metric in NUMERIC_METRICS:
        return "cagr" if _has(text, "연평균") else "percentage_change"
    if intent != "calc":
        if metric == "operating_profit" and _has(text, "영업이익률", "마진"):
            return "margin"
        return None
    if _has(text, "연평균성장률", "cagr"):
        return "cagr"
    if _has(text, "영업이익률", "마진"):
        return "margin"
    if _has(text, "비중", "비율"):
        return "ratio_percent"
    if _has(text, "증가율", "감소율", "성장률", "증감률", "증감", "대비", "얼마나늘", "얼마나줄"):
        return "percentage_change"
    if _has(text, "평균"):
        return "average"
    if _has(text, "최대", "가장큰", "가장많", "최고"):
        return "max"
    if _has(text, "최소", "가장작", "가장적", "최저"):
        return "min"
    if _has(text, "합계", "총합", "합산"):
        return "sum"
    if _has(text, "더하기", "더한", "합쳐", "합산"):
        return "add"
    if _has(text, "차이", "차감", "뺀", "감소액"):
        return "subtract"
    if _has(text, "곱", "곱하기"):
        return "multiply"
    if _has(text, "몇배", "배수", "나누기"):
        return "divide"
    return None


def infer_denominator_metric(
    metric: str | None,
    metric_matches: list[str],
    *,
    operation: str | None,
) -> str | None:
    """비중·마진 계산의 분모 지표를 metric_matches에서 고른다.

    "A 대비 B 비중"처럼 질의에 지표가 두 개 잡히면 slots.metric(분자)이 아닌
    나머지 하나가 분모다. 영업이익률(margin)은 분모가 항상 매출이다.
    """
    if operation == "margin":
        return "revenue"
    if operation != "ratio_percent":
        return None
    if not metric or len(metric_matches) < 2:
        return None
    others = [key for key in metric_matches if key != metric]
    return others[0] if len(others) == 1 else None


def build_calculation(
    intent: str,
    question: str,
    metric: str | None,
    *,
    denominator_metric: str | None = None,
    metric_matches: list[str] | None = None,
    compare_axis: str = "",
    operation_override: str | None = None,
) -> dict[str, Any]:
    # 파생 지표 사전이 지정한 연산은 질의문 단서보다 우선한다.
    operation = operation_override or operation_for(
        question, intent=intent, metric=metric, compare_axis=compare_axis
    )
    if operation is None:
        return {}
    if denominator_metric is None:
        denominator_metric = infer_denominator_metric(
            metric, list(metric_matches or []), operation=operation
        )
    result: dict[str, Any] = {"operation": operation}
    if metric:
        result["metric"] = metric
    if denominator_metric:
        result["denominator_metric"] = denominator_metric
    return result


__all__ = [
    "NUMERIC_METRICS",
    "SUPPORTED_OPERATIONS",
    "TWO_OPERAND_OPERATIONS",
    "TWO_PERIOD_OPERATIONS",
    "build_calculation",
    "canonical_question_type",
    "infer_denominator_metric",
    "operation_for",
]
