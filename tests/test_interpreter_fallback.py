from pathlib import Path

import pytest

from interpreter.index.corpus_index import CorpusIndex, InterpreterConfig
from interpreter.pipeline.build_intent import build_intent


@pytest.fixture
def index():
    config = InterpreterConfig.load(Path(__file__).parents[1] / "interpreter/config")
    config.aliases = {"ambiguous_tokens": ["삼성"]}
    config.sectors = {}
    rows = [dict(corp_name=name, corp_code=str(i), stock_code=str(i),
                 listed_name=name, sector="전자", listing_date="20000101")
            for i, name in enumerate(["삼성전자", "삼성SDI"])]
    return CorpusIndex(rows, [], config, Path("."))


class Client:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    def generate_json(self, messages, *, schema, operation):
        self.calls += 1
        assert operation == "interpreter_slot_fill"
        assert "denominator_metric" in messages[0]["content"]
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def test_missing_metric_updates_calculation(index):
    client = Client({"metric": "revenue"})
    result = build_intent("삼성전자 2024년 대비 2025년 외형 증가율", index, True, client)
    assert client.calls == 1
    assert result.llm_used
    assert result.calculation == {"operation": "percentage_change", "metric": "revenue"}


def test_missing_time_calls_fallback(index):
    client = Client({"years": [2025]})
    result = build_intent("삼성전자 이천이십오년 매출", index, True, client)
    assert client.calls == 1
    assert result.manifest_filter.base_years == [2025]


def test_ambiguous_company_is_resolved(index):
    client = Client({"corp_names": ["삼성전자"]})
    result = build_intent("삼성의 스마트폰 만드는 회사 2025년 매출", index, True, client)
    assert result.llm_used
    assert result.route == "ok"
    assert result.corps[0].corp_name == "삼성전자"


def test_unresolved_ambiguity_stays_clarification(index):
    result = build_intent("삼성 2025년 매출", index, True, Client({}))
    assert result.route == "need_clarify"


def test_event_metric_updates_search_group_and_time(index):
    result = build_intent("삼성전자 2025년 맺은 거래 알려줘", index, True,
                          Client({"metric": "supply_contract"}))
    assert result.llm_used
    assert result.manifest_filter.doc_group == "exchange"
    assert result.manifest_filter.rcept_from.startswith("2025")


@pytest.mark.parametrize("payload", [RuntimeError("offline"), [],
    {"corp_names": [{}], "metric": [], "years": [True, "2025"], "operation": {}}])
def test_failure_and_invalid_payload_preserve_rules(index, payload):
    result = build_intent("삼성전자 알 수 없는 항목", index, True, Client(payload))
    assert not result.llm_used
    assert result.corps[0].corp_name == "삼성전자"


def test_complete_question_does_not_call(index):
    client = Client(AssertionError("must not call"))
    result = build_intent("삼성전자 2025년 매출액", index, True, client)
    assert client.calls == 0
    assert not result.llm_used


def test_disabled_does_not_call(index):
    client = Client({"metric": "revenue"})
    build_intent("삼성전자 외형", index, False, client)
    assert client.calls == 0


def test_missing_denominator_is_filled_without_overwriting_metric(index):
    result = build_intent("삼성전자 2025년 매출 비중", index, True,
                          Client({"metric": "net_income", "denominator_metric": "total_assets"}))
    assert result.calculation == {
        "operation": "ratio_percent", "metric": "revenue", "denominator_metric": "total_assets"}


def test_sector_fallback_does_not_narrow_to_one_company(index):
    result = build_intent("전자 기업의 외형", index, True,
                          Client({"corp_names": ["삼성전자"], "metric": "revenue"}))
    assert not result.corps
    assert result.sector == "전자"


def test_environment_flag_accepts_true(index, monkeypatch):
    monkeypatch.setenv("INTERPRETER_USE_LLM", "true")
    client = Client({"metric": "revenue"})
    result = build_intent("삼성전자 외형", index, llm_client=client)
    assert client.calls == 1
    assert result.llm_used


def test_slot_token_budget_is_independent(monkeypatch):
    from integration.clova import ClovaChatClient
    client = ClovaChatClient(api_key="test")
    calls = []
    def request(messages, **kwargs):
        calls.append(kwargs)
        return '{}'
    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setenv("CLOVA_INTERPRETER_MAX_TOKENS", "1024")
    monkeypatch.setenv("CLOVA_SEMANTIC_MAX_TOKENS", "128")
    client.generate_json([], schema={}, operation="interpreter_slot_fill")
    client.generate_json([], schema={})
    assert [call["max_tokens"] for call in calls] == [1024, 128]
