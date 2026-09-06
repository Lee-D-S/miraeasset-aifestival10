from __future__ import annotations

import json
from typing import Any

from reasoner.contracts import ReasonerResult
from reasoner.validation import validate_submission_response


def to_submission_response(question_id: str, question: str, result: ReasonerResult) -> dict[str, str]:
    """Convert internal Reasoner output to the competition's five-string contract."""

    citations = result.citations
    context = "\n\n".join(
        f"[출처: {item.get('source', '')}][문서ID: {item.get('document_id', '')}]\n{item.get('evidence', item.get('text', ''))}"
        for item in citations
    )
    trace = {
        "status": result.status,
        "steps": result.trace,
        "fact_count": len(result.facts),
        "calculation_count": len(result.calculations),
        "citation_count": len(result.citations),
        "warnings": result.warnings,
    }
    response = {
        "question_id": str(question_id),
        "question": str(question),
        "retrieved_context": context,
        "think_trace": json.dumps(trace, ensure_ascii=False),
        "answer": str(result.answer),
    }
    valid, errors = validate_submission_response(response)
    if not valid:
        raise ValueError("제출 응답 형식이 올바르지 않습니다: " + ", ".join(errors))
    return response
