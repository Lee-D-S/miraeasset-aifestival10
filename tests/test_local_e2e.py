from __future__ import annotations

from integration.api import to_submission_response
from integration.testing import build_deterministic_pipeline


def test_deterministic_pipeline_runs_stage1_to_stage4_without_external_services():
    pipeline = build_deterministic_pipeline(
        intent={
            "route": "ok",
            "intent": "lookup",
            "question_type": "lookup",
            "normalized_question": "기업A 2025년 연결 매출액",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
            "manifest_filter": {"corp_names": ["기업A"], "base_years": [2025]},
        },
        documents=[{
            "id": "doc-e2e",
            "text": "기업A 2025년 연결 매출액 100억원입니다.",
            "source": "fixture.xml",
            "metadata": {
                "corp_name": "기업A",
                "doc_group": "periodic",
                "base_year": 2025,
                "base_month": 12,
                "basis": "연결",
            },
        }],
        vector_scores={"doc-e2e": 1.0},
    )

    state = pipeline.invoke(question_id="E2E-001", question="기업A 2025년 연결 매출액")
    response = to_submission_response(state)

    assert state["stage2_result"]["cited_documents"]
    assert state["stage3_result"]["status"] == "success"
    assert state["stage4_result"]["status"] == "success", state["stage4_result"]
    assert set(response) == {"question_id", "question", "retrieved_context", "think_trace", "answer"}
    assert "deterministic" not in response["think_trace"] or response["answer"]
