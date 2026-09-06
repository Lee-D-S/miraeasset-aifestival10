from pathlib import Path

import pytest

from interpreter.index.corpus_index import CorpusIndex, InterpreterConfig
from interpreter.pipeline.build_intent import build_intent
from reasoner.contracts import ReasonerDocument, adapt_interpreter_intent
from reasoner.grounding import requested_aggregation_scope, requested_periods


@pytest.fixture
def index():
    config = InterpreterConfig.load(Path(__file__).parents[1] / "interpreter/config")
    config.aliases = {}
    config.sectors = {}
    return CorpusIndex([dict(corp_name="삼성전자", corp_code="1", stock_code="1",
        listed_name="삼성전자", sector="전자", listing_date="20000101")], [], config, Path("."))


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def generate_json(self, messages, **kwargs):
        self.calls.append(kwargs.get("operation"))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


@pytest.mark.parametrize("phrase,operation", [
    ("더한 값", "add"), ("평균", "average"), ("차액", "subtract"),
    ("합계", "sum"), ("곱한 값", "multiply"), ("배수", "divide"),
])
def test_calculation_words_do_not_silently_become_lookup(index, phrase, operation):
    result = build_intent(f"삼성전자 2024년과 2025년 매출액의 {phrase}은?", index, False)
    assert result.question_type == "calculation"
    assert result.calculation["operation"] == operation


def test_amount_difference_wins_over_generic_comparison(index):
    result = build_intent("삼성전자 2024년 대비 2025년 매출액 차액은?", index, False)
    assert result.calculation["operation"] == "subtract"


def test_half_year_paraphrase(index):
    result = build_intent("삼성전자 2025년 첫 여섯 달 매출액은?", index, False)
    assert result.time.doc_subtype == "half"
    assert result.time.base_months == [6]


def test_receipt_year_does_not_constrain_fiscal_fact_year(index):
    result = build_intent("삼성전자가 2025년에 제출한 사업보고서 매출액은?", index, False)
    assert result.time.mode == "disclosure"
    assert result.manifest_filter.base_years == []
    assert result.manifest_filter.rcept_from == "20250101"
    assert requested_periods(adapt_interpreter_intent(result.to_dict())) == []


@pytest.mark.parametrize("phrase,mode,correction_filter", [
    ("정정본을 제외하고 원공시만", "original_only", False),
    ("정정 후 최종 내용만", "latest_only", None),
    ("정정 전후 변경이력", "include_chain", None),
    ("", "latest_only", None),
])
def test_correction_policy_reaches_retrieval(index, phrase, mode, correction_filter):
    result = build_intent(f"삼성전자 2025년 공급계약 {phrase} 알려줘", index, False)
    assert result.correction_mode == mode
    assert result.manifest_filter.is_correction is correction_filter


def test_basis_and_scope_paraphrases(index):
    result = build_intent("삼성전자 2025년 자회사를 빼고 본사만의 매출액은?", index, False)
    assert result.basis == "별도"
    result = build_intent("삼성전자 2025년 국내와 해외로 나눈 매출액은?", index, False)
    assert requested_aggregation_scope(adapt_interpreter_intent(result.to_dict())) == "region"


def test_unrecognized_conditions_trigger_review_and_propagate_all_fields(index):
    client = Client({"intent": "calc", "metric": "revenue", "operation": "average",
        "basis": "별도", "aggregation_scope": "region", "doc_subtype": "half"})
    result = build_intent("삼성전자 2025년 독립 실적의 산술중앙을 알려줘", index, True, client)
    assert client.calls == ["interpreter_slot_fill"]
    assert result.question_type == "calculation"
    assert result.basis == "별도"
    assert result.time.base_months == [6]
    assert requested_aggregation_scope(adapt_interpreter_intent(result.to_dict())) == "region"


def test_llm_period_is_subject_to_corpus_bounds(index):
    client = Client({"metric": "revenue", "years": [2026], "doc_subtype": "half"})
    result = build_intent("삼성전자 이천이십육년 첫 여섯 달 외형", index, True, client)
    assert result.route == "unanswerable"
    assert result.reject_reason == "out_of_corpus_period"


def test_llm_only_period_fills_months(index):
    client = Client({"metric": "revenue", "years": [2026], "doc_subtype": "half"})
    result = build_intent("삼성전자 이천이십육년 전반 실적", index, True, client)
    assert result.route == "unanswerable"


@pytest.mark.parametrize("payload", [{}, {"metric": "customer_churn"}, RuntimeError("offline")])
def test_unresolved_metric_never_runs_unconstrained_search(index, payload):
    result = build_intent("삼성전자 2025년 고객 이탈률", index, True, Client(payload))
    assert result.route == "need_clarify"
    assert result.manifest_filter.corp_names == []


def test_explicit_conditions_survive_conflicting_llm_fields(index):
    result = build_intent("삼성전자 2025년 별도 외형 원공시만", index, True,
        Client({"metric": "revenue", "basis": "연결", "correction_mode": "include_chain"}))
    assert result.basis == "별도"
    assert result.correction_mode == "original_only"


def test_unresolved_semantics_from_valid_json_requests_clarification(index):
    result = build_intent("삼성전자 2025년 매출액 중앙값", index, True,
        Client({"metric": "revenue", "intent": "calc", "unresolved_slots": ["operation"]}))
    assert result.route == "need_clarify"
    assert "operation" in result.missing_slots


def document(identifier, value, *, correction=False, receipt=None):
    return ReasonerDocument(id=identifier, source=identifier,
        text=f"2025년 연결 매출액 {value}억원", metadata={"corp_name": "삼성전자",
        "base_year": 2025, "base_month": 12, "doc_group": "periodic", "rcept_no": receipt or identifier,
        "rcept_dt": "20260302" if correction else "20260301", "is_correction": correction})


def test_latest_revision_filters_original_but_keeps_all_latest_chunks():
    from reasoner.deterministic.corrections import select_fact_documents
    docs = [document("old", 100), document("a", 200, correction=True, receipt="new"),
            document("b", 300, correction=True, receipt="new")]
    assert [d.id for d in select_fact_documents(docs, "latest_only")] == ["a", "b"]
    assert [d.id for d in select_fact_documents(docs, "original_only")] == ["old"]
    assert select_fact_documents(docs, "include_chain") == docs


def test_unknown_route_fails_closed():
    from integration.supervisor import DeterministicSupervisor
    for route in ("bogus", "", None):
        assert DeterministicSupervisor().decide(phase="after_interpreter", state={"route": route}).action == "fail_closed"


def test_reinterpretation_is_bounded_and_only_for_unresolved_intent():
    from integration.supervisor import DeterministicSupervisor
    supervisor = DeterministicSupervisor(allow_reinterpretation=True)
    state = {"intent": {"interpretation_uncertain": True}, "reasoner_result": {"status": "insufficient_evidence"}}
    assert supervisor.decide(phase="after_reasoner", state=state).action == "reinterpret_question"
    assert supervisor.decide(phase="after_reasoner", state={**state, "reinterpretation_attempts": 1}).action == "fail_closed"
    assert supervisor.decide(phase="after_reasoner", state={**state, "intent": {}}).action == "fail_closed"


def test_fact_recovery_verifies_quote_and_rejects_invented_value():
    from reasoner.agents.fact_recovery import FactRecovery
    doc = document("d", 150)
    intent = adapt_interpreter_intent({"route": "ok", "metric": "revenue", "basis": "연결",
        "time": {"years": [2025]}, "corps": [{"corp_name": "삼성전자"}]})
    item = {"document_id": "d", "evidence": doc.text, "label": "매출액", "kind": "numeric", "value_text": "150억원"}
    recovery = FactRecovery(Client({"facts": [item, {**item, "value_text": "999억원"}]}))
    facts = recovery.recover([doc], intent, [])
    assert len(facts) == 1 and facts[0].value == 150
    assert recovery.recover([doc], intent, []) == []
    assert recovery.client.calls == ["fact_recovery"]


def test_fact_recovery_is_wired_to_reasoner_node():
    from reasoner.node import build_reasoner_node
    doc = document("d", 150)
    doc = ReasonerDocument(id=doc.id, source=doc.source, metadata=doc.metadata,
                          text="2025년 연결 실적 규모 150억원")
    client = Client({"facts": [{"document_id": "d", "evidence": doc.text,
        "label": "실적 규모", "kind": "numeric", "value_text": "150억원"}]})
    node = build_reasoner_node(answer_client=client)
    result = node({"question": "삼성전자 2025년 매출액", "intent": {
        "route": "ok", "intent": "lookup", "question_type": "lookup", "metric": "revenue",
        "basis": "연결", "time": {"years": [2025]}, "corps": [{"corp_name": "삼성전자"}]},
        "retriever_result": {"cited_documents": [doc.to_dict()]}})["reasoner_result"]
    assert result["status"] == "success"
    assert "fact_recovery=accepted:1" in result["trace"]
    assert client.calls == ["fact_recovery"]


def test_reinterpretation_graph_retrieves_again_once():
    from integration import StageNodes, StagePipeline
    calls = []
    def interpreter(state):
        return {"route": "ok", "intent": {"route": "ok", "intent": "lookup",
            "question_type": "lookup", "missing_slots": ["time"]}}
    def reinterpret(state):
        calls.append("reinterpret")
        assert state["reinterpretation_attempts"] == 1
        return {"route": "ok", "intent": {"route": "ok", "intent": "lookup", "question_type": "lookup"}}
    def retrieve(state):
        calls.append("retrieve")
        return {"retriever_result": {"cited_documents": [{"id": "d"}]}}
    def reason(state):
        calls.append("reason")
        return {"reasoner_result": {"status": "success" if state.get("reinterpretation_attempts") else "insufficient_evidence"}}
    result = StagePipeline(StageNodes(interpreter=interpreter, reinterpreter=reinterpret,
        retriever=retrieve, reasoner=reason, validator=lambda s: {"validator_result": {"status": "pass"}})).invoke(
            question_id="recovery", question="원래 질문")
    assert calls == ["retrieve", "reason", "reinterpret", "retrieve", "reason"]
    assert result["question"] == result["original_question"] == "원래 질문"
    assert result["reinterpretation_attempts"] == 1


def test_reinterpretation_clears_old_results_and_plan():
    from integration.graph import _reinterpret_node
    node = _reinterpret_node(lambda s: {"route": "ok", "intent": {"metric": "revenue"}})
    result = node({"question": "q", "original_question": "q", "analysis_plan": {"status": "ready"},
        "facts": [{"value": 999}], "retriever_result": {"status": "ok"}, "planner_attempts": 1})
    assert result["analysis_plan"] is None and result["facts"] is None
    assert result["retriever_result"] is None and result["planner_attempts"] == 0


def test_planner_output_budget_is_independent(monkeypatch):
    from integration.clova import ClovaChatClient
    client = ClovaChatClient(api_key="test")
    calls = []
    def request(messages, **kwargs):
        calls.append(kwargs)
        return '{}'
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setenv("CLOVA_PLANNER_MAX_TOKENS", "2048")
    monkeypatch.setenv("CLOVA_SEMANTIC_MAX_TOKENS", "128")
    client.generate_json([], schema={}, operation="calculation_planning")
    assert calls[0]["max_tokens"] == 2048


def test_fact_recovery_does_not_relabel_prior_year_with_report_year():
    from reasoner.agents.fact_recovery import FactRecovery
    doc = document("d", 150)
    doc = ReasonerDocument(id=doc.id, source=doc.source, metadata=doc.metadata,
                          text="2024년 연결 매출액 150억원")
    item = {"document_id": "d", "evidence": doc.text, "label": "매출액", "kind": "numeric", "value_text": "150억원"}
    intent = adapt_interpreter_intent({"route": "ok", "metric": "revenue", "time": {"years": [2025]}})
    assert FactRecovery(Client({"facts": [item]})).recover([doc], intent, []) == []
