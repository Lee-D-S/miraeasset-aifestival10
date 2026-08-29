from __future__ import annotations

import math
import re
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent, Stage3Result
from stage3.deterministic.calculations import execute_operation


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?")


def _numbers(value: Any) -> list[float]:
    if value is None:
        return []
    result: list[float] = []
    for token in NUMBER_PATTERN.findall(str(value)):
        try:
            result.append(float(token.replace(",", "")))
        except ValueError:
            continue
    return result


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=1e-6, abs_tol=1e-6)


def _calculation_value(calculation: dict[str, Any]) -> float | None:
    values = calculation.get("inputs", [])
    parsed: list[float] = []
    periods: list[str] = []
    for value in values:
        if isinstance(value, dict):
            parsed.extend(_numbers(value.get("value")))
            if value.get("period"):
                periods.append(str(value["period"]))
        else:
            parsed.extend(_numbers(value))
    operation = str(calculation.get("operation", ""))
    try:
        if operation == "percentage_change":
            return execute_operation(operation, parsed)
        if operation == "cagr":
            years = abs(int(periods[1][:4]) - int(periods[0][:4])) if len(periods) >= 2 else None
            return execute_operation(operation, parsed, periods=years)
        if operation == "ratio_percent":
            return execute_operation("ratio", parsed) * 100
        if operation == "margin":
            return execute_operation("margin", parsed)
        if operation in {"sum", "average", "min", "max"}:
            return execute_operation(operation, parsed)
    except (TypeError, ValueError, IndexError, ZeroDivisionError):
        return None
    return None


def _answer_number_warnings(result: Stage3Result) -> list[str]:
    allowed: list[float] = []
    periods: set[str] = set()
    for fact in result.facts:
        allowed.extend(_numbers(fact.get("value")))
        allowed.extend(_numbers(fact.get("raw_value")))
        allowed.extend(_numbers(fact.get("normalized_value")))
        if fact.get("period"):
            period = str(fact["period"])
            periods.add(period)
            allowed.extend(_numbers(period))
    for calculation in result.calculations:
        allowed.extend(_numbers(calculation.get("result")))
        for value in calculation.get("inputs", []):
            allowed.extend(_numbers(value.get("value") if isinstance(value, dict) else value))
    warnings: list[str] = []
    answer = result.answer or ""
    for match in NUMBER_PATTERN.finditer(answer):
        token = match.group(0)
        value = float(token.replace(",", ""))
        context = answer[max(0, match.start() - 18): min(len(answer), match.end() + 18)]
        if any(marker in context.lower() for marker in ("문서id", "rcept", "접수번호", "document_id", "source")):
            continue
        if re.search(r"\d\s*위", answer[match.start(): min(len(answer), match.end() + 4)]):
            continue
        if any(_close(value, candidate) for candidate in allowed):
            continue
        if len(token.replace(",", "")) == 4 and any(period.startswith(token) for period in periods):
            continue
        warnings.append(f"답변에 근거·계산 결과에 없는 숫자가 있습니다: {token}")
    return warnings


def validate_stage3_result(result: Stage3Result, intent: Stage3Intent) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if intent.route != "ok":
        if result.citations or result.facts or result.calculations or result.comparison_results or result.linked_events:
            warnings.append("처리 불가 route에서 근거 또는 분석 결과가 생성되었습니다.")
        return not warnings, warnings

    fact_ids = {str(fact.get("document_id", "")) for fact in result.facts if fact.get("document_id")}
    citation_ids = {str(item.get("document_id", "")) for item in result.citations if item.get("document_id")}
    for fact in result.facts:
        if fact.get("document_id") not in citation_ids:
            warnings.append(f"Fact의 근거 문서가 citations에 없습니다: {fact.get('document_id')}")
    known_ids = fact_ids | citation_ids
    for calculation in result.calculations:
        evidence_ids = set(map(str, calculation.get("evidence_ids", [])))
        if calculation.get("status") == "ok":
            if not evidence_ids.issubset(known_ids):
                warnings.append("계산 결과가 알 수 없는 근거 문서를 참조합니다.")
            recomputed = _calculation_value(calculation)
            if recomputed is None or not _close(float(calculation.get("result")), recomputed):
                warnings.append(f"계산 결과 재검증 실패: {calculation.get('operation')}")
    for comparison in result.comparison_results:
        if comparison.get("status") == "ok" and not set(map(str, comparison.get("evidence_ids", []))).issubset(known_ids):
            warnings.append("비교 결과가 알 수 없는 근거 문서를 참조합니다.")
    if result.status == "success" and not result.answer.strip():
        warnings.append("success 상태의 답변이 비어 있습니다.")
    warnings.extend(_answer_number_warnings(result))
    return not warnings, warnings


def validate_submission_response(response: dict[str, Any]) -> tuple[bool, list[str]]:
    required = ("question_id", "question", "retrieved_context", "think_trace", "answer")
    errors: list[str] = []
    if set(response) != set(required):
        errors.append("제출 응답 필드가 정확히 5개가 아닙니다.")
    for field in required:
        if not isinstance(response.get(field), str):
            errors.append(f"{field}는 string이어야 합니다.")
    return not errors, errors
