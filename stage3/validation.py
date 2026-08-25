from __future__ import annotations

import re
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent, Stage3Result


NUMBER_PATTERN = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:[,.]\d+)?")


def validate_stage3_result(result: Stage3Result, intent: Stage3Intent) -> tuple[bool, list[str]]:
    warnings: list[str] = []
    if intent.route != "ok":
        if result.citations or result.facts or result.calculations:
            warnings.append("처리 불가 route에서 근거 또는 계산 결과가 생성되었습니다.")
    fact_ids = {str(fact.get("document_id", "")) for fact in result.facts if fact.get("document_id")}
    citation_ids = {str(item.get("document_id", "")) for item in result.citations if item.get("document_id")}
    for fact in result.facts:
        if fact.get("document_id") not in citation_ids:
            warnings.append(f"Fact의 근거 문서가 citations에 없습니다: {fact.get('document_id')}")
    for calculation in result.calculations:
        if calculation.get("status") == "ok" and not set(map(str, calculation.get("evidence_ids", []))).issubset(fact_ids | citation_ids):
            warnings.append("계산 결과가 알 수 없는 근거 문서를 참조합니다.")
    if result.status == "success" and not result.answer.strip():
        warnings.append("success 상태의 답변이 비어 있습니다.")
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
