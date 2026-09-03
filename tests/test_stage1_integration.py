from __future__ import annotations

import os
from pathlib import Path

import pytest

from integration import StageNodes, StagePipeline
from stage1 import build_intent, build_stage1_node
from stage1.index.corpus_index import CorpusIndex

# Needs the supplied DART corpus -- runs on the self-hosted runner, skipped by
# hosted CI (see pytest.ini `needs_corpus`).
pytestmark = pytest.mark.needs_corpus

# CORPUS_DIR overrides; the fallback is one contributor's local checkout.
CORPUS = Path(
    os.environ.get("CORPUS_DIR")
    or r"C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시\corpus"
)


def test_stage1_node_emits_shared_state_partial_update():
    node = build_stage1_node(corpus_dir=CORPUS, use_llm=False)
    update = node({"question_id": "Q-1", "question": "삼성전자의 2025년 매출액은?", "answer": "unchanged"})

    assert set(update) == {"intent", "route"}
    assert update["route"] == "ok"
    assert update["intent"]["intent"] == "lookup"
    assert update["intent"]["question_type"] == "lookup"
    assert update["intent"]["manifest_filter"]["corp_names"]


def test_stage1_calc_intent_contains_stage3_operation():
    index = CorpusIndex.load(corpus_dir=CORPUS)
    intent = build_intent("삼성전자의 2024년 대비 2025년 매출액 증가율은?", index, use_llm=False)

    assert intent.intent == "calc"
    assert intent.question_type == "calculation"
    assert intent.calculation == {"operation": "percentage_change", "metric": "revenue"}


def test_stage1_compare_maps_to_stage3_rank_operation():
    index = CorpusIndex.load(corpus_dir=CORPUS)
    intent = build_intent("삼성전자와 SK하이닉스 중 2025년 매출액이 가장 큰 곳은?", index, use_llm=False)

    assert intent.intent == "compare"
    assert intent.question_type == "compare"
    assert intent.calculation["operation"] == "rank"


def test_stage1_blocked_route_skips_downstream_nodes():
    calls: list[str] = []
    stage1 = build_stage1_node(corpus_dir=CORPUS, use_llm=False)

    def stage2(_state):
        calls.append("stage2")
        raise AssertionError("blocked Stage1 route must skip Stage2")

    def stage3(_state):
        calls.append("stage3")
        raise AssertionError("blocked Stage1 route must skip Stage3")

    def stage4(state):
        calls.append("stage4")
        return {"stage4_result": {"status": state["route"]}, "answer": "차단"}

    state = StagePipeline(StageNodes(stage1, stage2, stage3, stage4)).invoke(
        question_id="Q-2", question="삼성전자 지금 사도 되나?"
    )
    assert calls == ["stage4"]
    assert state["route"] == "unsafe"


class SlotClient:
    def generate_json(self, _messages, *, schema):
        assert schema["type"] == "object"
        return {"corp_names": ["삼성전자"], "metric": "revenue", "intent": "lookup", "years": [2025]}


def test_stage1_slot_llm_is_injected_and_validated():
    index = CorpusIndex.load(corpus_dir=CORPUS)
    intent = build_intent("2025년 매출액은?", index, use_llm=True, llm_client=SlotClient())

    assert intent.llm_used is True
    assert intent.intent == "lookup"
    assert intent.corps[0].corp_name == "삼성전자"


class FailingSlotClient:
    def generate_json(self, _messages, *, schema):
        raise RuntimeError("test failure")


def test_stage1_slot_llm_failure_keeps_rule_result():
    index = CorpusIndex.load(corpus_dir=CORPUS)
    intent = build_intent("삼성전자의 2025년 매출액은?", index, use_llm=True, llm_client=FailingSlotClient())

    assert intent.llm_used is False
    assert intent.corps[0].corp_name == "삼성전자"
