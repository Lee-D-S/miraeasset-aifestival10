from __future__ import annotations

from stage4.node import build_stage4_node
from stage4.numeric import extract_answer_numbers, validate_numeric_answer
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


def test_extracts_korean_units_and_ignores_dates_and_ranks():
    values = extract_answer_numbers("2025년 1위 매출은 -1.2억원(120,000,000원)입니다.")
    assert [item["value"] for item in values] == [-120000000, 120000000]


def test_numeric_validation_uses_normalized_fact_once():
    result = validate_numeric_answer("매출은 1.2억원입니다.", _stage3())
    assert result["pass"] is True
    assert result["matched_count"] == 1


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
    assert update["answer"] == "제공된 공시 근거만으로 답변을 검증할 수 없습니다."
    assert client.text_calls == 0
    assert client.json_calls == 1


def test_blocked_route_uses_deterministic_answer_without_llm():
    update = build_stage4_node()({"route": "unsafe", "answer": "무시"})
    assert update["stage4_result"]["status"] == "unsafe"
    assert update["answer"] == "공시 근거만으로 답변할 수 없는 요청입니다."


def test_stage4_fails_closed_when_llm_is_unavailable():
    update = build_stage4_node()({
        "route": "ok",
        "question": "매출은?",
        "intent": {"question_type": "lookup"},
        "answer": "매출은 1.2억원입니다. [source:doc-1]",
        "stage3_result": _stage3(),
    })
    assert update["stage4_result"]["status"] == "validation_failed"
    assert update["answer"] == "제공된 공시 근거만으로 답변을 검증할 수 없습니다."


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
