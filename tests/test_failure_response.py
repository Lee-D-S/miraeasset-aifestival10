from __future__ import annotations

import json

from integration.api import to_submission_response
from integration.failure_response import MAX_FAILURE_ANSWER_CHARS, build_failure_response
from stage4.node import build_stage4_node


def test_out_of_corpus_period_explains_period_and_guidance():
    update = build_stage4_node()({
        "route": "unanswerable",
        "intent": {
            "route": "unanswerable",
            "reject_reason": "out_of_corpus_year",
            "time": {"out_of_range_years": [2022]},
        },
    })

    assert update["answer"].startswith("제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.")
    assert "2022년" in update["answer"]
    assert "2023년부터 2026년 1분기" in update["answer"]
    assert update["stage4_result"]["failure_reason_code"] == "OUT_OF_CORPUS_PERIOD"


def test_ambiguous_entity_does_not_echo_internal_rejection_reason():
    response = build_failure_response({
        "route": "need_clarify",
        "intent": {
            "reject_reason": "ambiguous_corp",
            "ambiguous_mentions": [{"token": "삼성", "internal": "guard detail"}],
        },
    })

    assert response.reason_code.value == "AMBIGUOUS_ENTITY"
    assert "삼성" in response.answer
    assert "공식 기업명 또는 종목명" in response.answer
    assert "ambiguous_corp" not in response.answer
    assert "guard detail" not in response.answer


def test_missing_slots_are_explained_without_an_extra_model_call():
    response = build_failure_response({
        "route": "need_clarify",
        "intent": {"missing_slots": ["time", "metric"]},
    })

    assert response.reason_code.value == "MISSING_REQUIRED_SLOT"
    assert "기간·확인할 항목" in response.answer
    assert "기업명·기간·확인할 항목" in response.answer


def test_missing_stage3_result_is_a_validation_failure_with_reason():
    update = build_stage4_node()({"route": "ok", "question": "매출은?"})

    assert update["stage4_result"]["status"] == "validation_failed"
    assert update["stage4_result"]["failure_reason_code"] == "VALIDATION_FAILED"
    assert "Stage3 분석 결과가 없습니다." in update["answer"]


def test_need_clarify_route_without_details_keeps_clarification_conclusion():
    response = build_failure_response({"route": "need_clarify"})

    assert response.reason_code.value == "MISSING_REQUIRED_SLOT"
    assert response.answer.startswith("질문의 기업·기간·기준이 명확하지 않습니다.")
    assert "기업명·기간·확인할 항목" in response.answer


def test_no_retrieval_evidence_has_actionable_guidance():
    response = build_failure_response({
        "route": "ok",
        "question": "삼성전자의 매출액은?",
        "intent": {"metric": "revenue"},
        "stage2_result": {"documents": [], "cited_documents": []},
        "stage3_result": {"status": "insufficient_evidence", "facts": [], "citations": []},
    })

    assert response.reason_code.value == "NO_RETRIEVAL_EVIDENCE"
    assert "매출액" in response.answer
    assert "근거 문서를 검색하지 못했습니다" in response.answer
    assert "기업·기간·공시 유형·지표" in response.answer


def test_calculation_failure_is_distinguished_from_no_documents():
    response = build_failure_response({
        "route": "ok",
        "stage2_result": {"documents": [{"id": "doc-1"}]},
        "stage3_result": {
            "calculations": [{"status": "error"}],
            "facts": [],
        },
    })

    assert response.reason_code.value == "INSUFFICIENT_FACT_EVIDENCE"
    assert "계산에 필요한 기간·단위·기준" in response.answer
    assert "연도와 연결·별도 기준" in response.answer


def test_semantic_validation_failure_explains_grounding_without_provider_details():
    response = build_failure_response(
        {
            "route": "ok",
            "stage2_result": {"documents": [{"id": "doc-1"}]},
            "stage3_result": {"facts": [{"metric": "revenue"}], "citations": [{"document_id": "doc-1"}]},
        },
        numeric_check={"pass": True},
        citation_check={"pass": True},
        semantic_check={
            "pass": False,
            "unsupported_claims": ["provider-only diagnostic"],
            "missing_aspects": [],
            "summary": "raw provider response should not be public",
        },
    )

    assert response.reason_code.value == "VALIDATION_FAILED"
    assert "질문의 요구사항과 공시 근거" in response.answer
    assert "provider-only diagnostic" not in response.answer
    assert "raw provider response" not in response.answer


def test_failure_answer_is_bounded_and_does_not_expose_provider_details():
    response = build_failure_response({
        "route": "ok",
        "stage4_result": {
            "status": "validation_failed",
            "provider_status": {
                "api_key": "secret-value",
                "error": "HTTP 429 with full provider response",
            },
        },
        "stage3_result": {"facts": [{"metric": "revenue"}]},
    })

    assert len(response.answer) <= MAX_FAILURE_ANSWER_CHARS
    assert "secret-value" not in response.answer
    assert "HTTP 429" not in response.answer
    assert "provider" not in response.answer.lower()


def test_trace_contains_reason_code_but_submission_schema_stays_at_five_strings():
    state = {
        "question_id": "Q-FAIL-1",
        "question": "2022년 매출액은?",
        "answer": "제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.\n\n이유: 요청하신 2022년은 제공된 공시 범위 밖입니다.",
        "stage4_result": {
            "status": "unanswerable",
            "failure_reason_code": "OUT_OF_CORPUS_PERIOD",
            "warnings": [],
            "trace": ["blocked_route"],
        },
    }

    response = to_submission_response(state)

    assert set(response) == {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    assert all(isinstance(value, str) for value in response.values())
    assert "OUT_OF_CORPUS_PERIOD" in json.loads(response["think_trace"])["stage4_result"]["failure_reason_code"]
    assert "failure_reason" not in response
