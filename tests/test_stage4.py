from __future__ import annotations

from stage3.contracts import adapt_stage1_intent
from stage4.node import build_stage4_node
from stage4.numeric import extract_answer_numbers, validate_numeric_answer
from integration.rate_limit import RateLimitBlocked
from integration import StageNodes, StagePipeline
from integration.api import to_submission_response


def _stage3(answer: str = "매출은 1.2억원입니다.") -> dict:
    return {
        "status": "success",
        "answer": answer,
        "facts": [{
            "metric": "revenue",
            "label": "매출액",
            "value": 1.2,
            "normalized_value": 120000000,
            "unit": "억원",
            "period": "2025",
            "basis": "연결",
            "document_id": "doc-1",
            "source": "공시.xml",
            "evidence": "매출액 1.2억원",
        }],
        "calculations": [],
        "comparison_results": [],
        "linked_events": [],
        "citations": [{
            "document_id": "doc-1",
            "source": "공시.xml",
            "evidence": "매출액 1.2억원",
        }],
    }


class SemanticClient:
    def __init__(self, verdict: dict | None = None, regenerated: str = "매출은 1.2억원입니다."):
        self.verdict = verdict or {
            "pass": True,
            "issues": [],
            "unsupported_claims": [],
            "missing_aspects": [],
            "summary": "근거와 의미가 일치합니다.",
        }
        self.regenerated = regenerated
        self.json_calls = 0
        self.text_calls = 0

    def generate_json(self, _messages, *, schema):
        self.json_calls += 1
        return self.verdict

    def generate_text(self, _messages):
        self.text_calls += 1
        return self.regenerated


class RateLimitedSemanticClient(SemanticClient):
    def generate_json(self, _messages, *, schema):
        self.json_calls += 1
        raise RateLimitBlocked("CLOVA remaining tokens가 요청 예산보다 작아 호출을 차단했습니다.")


class FailingSemanticClient(SemanticClient):
    def generate_json(self, _messages, *, schema):
        self.json_calls += 1
        raise ValueError("malformed provider response")


def test_extracts_korean_units_and_ignores_dates_and_ranks():
    values = extract_answer_numbers("2025년 1위 매출은 -1.2억원(120,000,000원)입니다.")
    assert [item["value"] for item in values] == [-120000000, 120000000]


def test_extracts_compound_jo_eok_amount_as_a_single_value():
    values = extract_answer_numbers("삼성전자의 2024-12 연결 매출은 300조 8,709억원입니다.")
    assert [item["value"] for item in values] == [300870900000000]


def test_ignores_hyphenated_period_and_contract_date_tokens():
    assert extract_answer_numbers("삼성전자의 2024-12 연결 매출은 얼마인가요?") == []
    assert extract_answer_numbers("계약기간은 2025-08-14부터입니다.") == []


def test_numeric_validation_matches_compound_jo_eok_fact_value():
    stage3_result = _stage3(answer="")
    stage3_result["facts"][0]["normalized_value"] = 300_870_900_000_000
    stage3_result["facts"][0]["value"] = "300조 8,709억원"
    result = validate_numeric_answer(
        "삼성전자의 2024-12 연결 매출은 300조 8,709억원입니다.", stage3_result
    )
    assert result["pass"] is True
    assert result["matched_count"] == 1


def test_numeric_validation_uses_normalized_fact_once():
    result = validate_numeric_answer("매출은 1.2억원입니다.", _stage3())
    assert result["pass"] is True
    assert result["matched_count"] == 1


def _stage3_with_breakdown_fact(answer: str) -> dict:
    return {
        "status": "success",
        "answer": answer,
        "facts": [
            {
                "metric": "revenue", "label": "매출액", "value": 100, "normalized_value": 10_000_000_000,
                "unit": "억원", "period": "2025-12", "basis": "연결", "company": "기업A",
                "document_id": "doc-total", "source": "공시.xml", "evidence": "매출액 100억원",
                "aggregation_scope": "total",
            },
            {
                "metric": "revenue", "label": "매출", "value": 30, "normalized_value": 3_000_000_000,
                "unit": "억원", "period": "2025-12", "basis": "연결", "company": "기업A",
                "document_id": "doc-breakdown", "source": "공시.xml", "evidence": "국내 매출 30억원",
                "table_context": {"row_label": "국내 매출"}, "aggregation_scope": "not_total",
            },
        ],
        "calculations": [],
        "comparison_results": [],
        "linked_events": [],
        "citations": [
            {"document_id": "doc-total", "source": "공시.xml", "evidence": "매출액 100억원"},
            {"document_id": "doc-breakdown", "source": "공시.xml", "evidence": "국내 매출 30억원"},
        ],
    }


def test_numeric_validation_rejects_a_non_matching_breakdown_fact_even_though_it_was_extracted():
    intent = {
        "question_type": "lookup", "metric": "revenue",
        "companies": ["기업A"], "basis": "연결", "time": {"years": [2025]},
    }
    stage3_result = _stage3_with_breakdown_fact("매출액은 3000000000원입니다.")

    without_intent = validate_numeric_answer(stage3_result["answer"], stage3_result)
    with_intent = validate_numeric_answer(
        stage3_result["answer"], stage3_result, adapt_stage1_intent(intent)
    )

    assert without_intent["pass"] is True
    assert with_intent["pass"] is False


def test_stage4_rejects_answer_grounded_only_in_a_non_matching_breakdown_fact():
    client = SemanticClient()
    stage3_result = _stage3_with_breakdown_fact("매출액은 3000000000원입니다. [source:doc-breakdown]")

    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "기업A의 2025년 연결 매출액은?",
        "intent": {
            "question_type": "lookup", "metric": "revenue",
            "companies": ["기업A"], "basis": "연결", "time": {"years": [2025]},
        },
        "answer": stage3_result["answer"],
        "stage3_result": stage3_result,
    })

    assert update["stage4_result"]["status"] == "validation_failed"
    assert update["stage4_result"]["numeric_check"]["pass"] is False
    assert any("3000000000" in error for error in update["stage4_result"]["numeric_check"]["errors"])


def test_stage4_passes_verified_answer():
    client = SemanticClient()
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["answer"] == "매출은 1.2억원입니다. [source:doc-1]"
    assert update["stage4_result"]["status"] == "success"
    assert client.text_calls == 0


def test_stage4_rejects_citation_not_present_in_stage2_results():
    client = SemanticClient()
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage2_result": {"documents": [{"id": "other-doc", "text": "다른 근거"}]},
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "validation_failed"
    assert client.text_calls == 0
    assert client.json_calls == 1


def test_stage4_only_validates_and_does_not_regenerate():
    client = SemanticClient(regenerated="매출은 1.2억원입니다.")
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "validation_failed"
    assert update["stage4_result"]["regenerated"] is False
    assert client.text_calls == 0
    assert client.json_calls == 1


def test_stage4_accepts_explicit_grounding_when_semantic_provider_is_uncertain():
    client = SemanticClient(verdict={
        "pass": False,
        "issues": ["provider was uncertain"],
        "unsupported_claims": [],
        "missing_aspects": [],
        "summary": "uncertain",
    })
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출액은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출액은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })

    assert update["stage4_result"]["status"] == "success"
    assert update["stage4_result"]["semantic_check"]["pass"] is True


def test_stage4_fails_closed_when_answer_is_invalid():
    client = SemanticClient(regenerated="매출은 9억원입니다.")
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "validation_failed"
    assert update["answer"].startswith("제공된 공시 근거만으로 답변을 검증할 수 없습니다.")
    assert "수치가 공시 근거와 일치하는지" in update["answer"]
    assert update["stage4_result"]["failure_reason_code"] == "VALIDATION_FAILED"
    assert client.text_calls == 0
    assert client.json_calls == 1


def test_blocked_route_uses_deterministic_answer_without_llm():
    update = build_stage4_node()({"route": "unsafe", "answer": "무시"})
    assert update["stage4_result"]["status"] == "unsafe"
    assert update["answer"].startswith("공시 근거만으로 답변할 수 없는 요청입니다.")
    assert "제공된 공시 기반 사실 확인 범위를 벗어납니다." in update["answer"]
    assert "다시 질문하려면:" not in update["answer"]
    assert update["stage4_result"]["failure_reason_code"] == "UNSAFE_REQUEST"


def test_stage4_uses_local_gate_when_llm_is_unavailable():
    update = build_stage4_node()({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "success"
    assert update["stage4_result"]["semantic_check"]["mode"] == "deterministic_fallback"
    assert "semantic_deterministic_fallback" in update["stage4_result"]["trace"]
    assert update["answer"] == "매출은 1.2억원입니다. [source:doc-1]"


def test_stage4_keeps_grounded_answer_when_semantic_provider_is_rate_limited():
    client = RateLimitedSemanticClient()
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "success"
    assert update["answer"] == "매출은 1.2억원입니다. [source:doc-1]"
    assert update["stage4_result"]["provider_status"]["status"] == "rate_limited"
    assert "provider_rate_limited_deterministic_grounding" in update["stage4_result"]["trace"]


def test_stage4_falls_back_for_malformed_semantic_provider_response():
    client = FailingSemanticClient()
    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })

    assert update["stage4_result"]["status"] == "success"
    assert update["stage4_result"]["provider_status"]["status"] == "provider_error"
    assert "semantic_provider_deterministic_fallback" in update["stage4_result"]["trace"]


def test_stage4_checks_multi_query_fact_gate_per_subquery_and_allows_partial_success():
    client = SemanticClient()
    stage3_result = _stage3("매출은 1.2억원입니다. [source:doc-1]")
    stage3_result["facts"][0]["aggregation_scope"] = "total"
    stage3_result["subresults"] = [
        {"subquery_id": "subquery-1", "status": "success", "facts": stage3_result["facts"]},
        {"subquery_id": "subquery-2", "status": "insufficient_evidence", "facts": []},
    ]
    intent = {
        "question_type": "multi_query",
        "metric": "revenue",
        "query_plan": [
            {"subquery_id": "subquery-1", "metric": "revenue", "question_type": "lookup", "manifest_filter": {}},
            {"subquery_id": "subquery-2", "metric": "operating_profit", "question_type": "lookup", "manifest_filter": {}},
        ],
    }

    update = build_stage4_node(validator_client=client)({
        "route": "ok",
        "question": "매출액과 영업이익은?",
        "intent": intent,
        "answer": stage3_result["answer"],
        "stage3_result": stage3_result,
    })

    assert update["stage4_result"]["status"] == "success"
    assert any("subquery-2" in error for error in update["stage4_result"]["numeric_check"]["errors"])


def test_stage4_runs_in_shared_langgraph_and_preserves_api_contract():
    client = SemanticClient()
    nodes = StageNodes(
        stage1=lambda _state: {"intent": {"route": "ok"}, "route": "ok"},
        stage2=lambda _state: {"stage2_result": {"documents": [{"id": "doc-1", "text": "evidence"}], "cited_documents": [{"id": "doc-1", "text": "evidence"}]}},
        stage3=lambda _state: {
            "stage3_result": _stage3(),
            "answer": "매출은 1.2억원입니다. [source:doc-1]",
            "context": "",
            "messages": [],
        },
        stage4=build_stage4_node(validator_client=client),
    )
    state = StagePipeline(nodes).invoke(question_id="Q-004", question="매출은?")
    response = to_submission_response(state)
    assert state["stage4_result"]["status"] == "success"
    assert set(response) == {"question_id", "question", "retrieved_context", "think_trace", "answer"}
    assert all(isinstance(value, str) for value in response.values())
