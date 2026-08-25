from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from agentic_rag.deterministic.calculation_registry import execute_operation

from stage3.contracts import Stage3Fact, Stage3Intent


def numeric_facts(facts: Iterable[Stage3Fact], *, metric: str | None = None) -> list[Stage3Fact]:
    return [
        fact for fact in facts
        if fact.kind == "numeric"
        and isinstance(fact.normalized_value, (int, float))
        and (metric is None or fact.metric == metric)
    ]


def _same_basis_period(left: Stage3Fact, right: Stage3Fact) -> bool:
    return left.period == right.period and left.basis == right.basis and left.unit == right.unit


def _ordered_values(facts: list[Stage3Fact]) -> list[Stage3Fact]:
    return sorted(facts, key=lambda fact: (str(fact.period or ""), str(fact.company or ""), fact.document_id))


def calculate_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent, *, operation: str | None = None) -> dict[str, Any]:
    """Run a whitelisted calculation over normalized grounded facts."""

    selected = numeric_facts(facts, metric=intent.metric)
    operation = operation or _infer_operation(intent)
    if not selected:
        return {"status": "insufficient_evidence", "error": "계산에 필요한 수치 근거가 없습니다.", "operation": operation}

    if operation in {"compare", "rank"}:
        return _compare_values(selected, operation)
    if operation == "percentage_change":
        ordered = _ordered_values(selected)
        if len(ordered) < 2:
            return {"status": "insufficient_evidence", "error": "증감률 계산에 필요한 두 기간의 수치가 없습니다.", "operation": operation}
        old, new = ordered[-2:]
        if not _same_basis_period(old, new) and old.basis != new.basis:
            return {"status": "invalid_basis", "error": "증감률 입력값의 연결/별도 기준이 다릅니다.", "operation": operation}
        if old.unit != new.unit:
            return {"status": "invalid_unit", "error": "증감률 입력값의 단위가 다릅니다.", "operation": operation}
        result = execute_operation(operation, [float(old.normalized_value), float(new.normalized_value)])
        return _calculation_result(operation, [old, new], result, "(new-old)/abs(old)*100", "%")
    if operation == "ratio_percent":
        if len(selected) < 2:
            return {"status": "insufficient_evidence", "error": "비중 계산에 필요한 분자·분모가 없습니다.", "operation": operation}
        numerator, denominator = selected[:2]
        if numerator.basis != denominator.basis or numerator.period != denominator.period:
            return {"status": "invalid_comparison", "error": "비중 입력값의 기간 또는 기준이 다릅니다.", "operation": operation}
        result = execute_operation("ratio", [float(numerator.normalized_value), float(denominator.normalized_value)]) * 100
        return _calculation_result(operation, [numerator, denominator], result, "numerator/denominator*100", "%")
    if operation in {"sum", "average", "min", "max"}:
        values = [float(fact.normalized_value) for fact in selected]
        result = execute_operation(operation, values)
        return _calculation_result(operation, selected, result, operation, selected[0].unit)
    return {"status": "unsupported", "error": f"지원하지 않는 계산 연산입니다: {operation}", "operation": operation}


def _infer_operation(intent: Stage3Intent) -> str:
    question = intent.normalized_question
    if intent.intent in {"compare", "comparison"} or any(word in question for word in ("비교", "어느 기업", "가장 큰", "순위")):
        return "rank"
    if any(word in question for word in ("증감률", "증가율", "감소율", "성장률")):
        return "percentage_change"
    if "비중" in question or "비율" in question:
        return "ratio_percent"
    if "합계" in question or "총액" in question:
        return "sum"
    return "compare" if intent.intent in {"compare", "comparison"} else "lookup"


def _calculation_result(operation: str, facts: list[Stage3Fact], result: float, formula: str, unit: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "operation": operation,
        "inputs": [fact.normalized_value for fact in facts],
        "formula": formula,
        "result": result,
        "unit": unit,
        "evidence_ids": list(dict.fromkeys(fact.document_id for fact in facts)),
    }


def _compare_values(facts: list[Stage3Fact], operation: str) -> dict[str, Any]:
    by_company: dict[str, Stage3Fact] = {}
    for fact in facts:
        company = fact.company or "미상"
        current = by_company.get(company)
        if current is None or (fact.confidence, str(fact.period or "")) > (current.confidence, str(current.period or "")):
            by_company[company] = fact
    if len(by_company) < 2:
        return {"status": "insufficient_evidence", "error": "비교에 필요한 서로 다른 기업의 수치가 없습니다.", "operation": operation}
    ranked = sorted(by_company.values(), key=lambda fact: float(fact.normalized_value), reverse=True)
    results = [
        {
            "rank": index,
            "company": fact.company or "미상",
            "value": fact.normalized_value,
            "unit": fact.unit,
            "period": fact.period,
            "basis": fact.basis,
            "document_id": fact.document_id,
        }
        for index, fact in enumerate(ranked, start=1)
    ]
    return {
        "status": "ok",
        "operation": operation,
        "results": results,
        "top": results[0],
        "evidence_ids": [item["document_id"] for item in results],
    }
