"""1단계 성공 기준은 답변 정확도가 아니라 route + manifest_filter 일치다.

    pytest interpreter/tests
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from interpreter.index.corpus_index import CorpusIndex
from interpreter.pipeline.build_intent import build_intent

# Gold cases need the supplied DART corpus (CorpusIndex.load()); run on the
# self-hosted runner, skipped by hosted CI (see pytest.ini `needs_corpus`).
pytestmark = pytest.mark.needs_corpus

GOLD_PATH = Path(__file__).resolve().parent / "gold_queries.jsonl"


def _load_gold() -> list[dict]:
    lines = GOLD_PATH.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture(scope="module")
def index() -> CorpusIndex:
    return CorpusIndex.load()


def _assert_subset(expected, actual, path: str = "") -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: dict 기대, 실제 {type(actual)}"
        for key, value in expected.items():
            _assert_subset(value, actual.get(key), f"{path}.{key}" if path else key)
    elif isinstance(expected, list):
        assert sorted(map(str, expected)) == sorted(map(str, actual or [])), (
            f"{path}: expected={expected} actual={actual}"
        )
    else:
        assert expected == actual, f"{path}: expected={expected!r} actual={actual!r}"


@pytest.mark.parametrize("case", _load_gold(), ids=lambda c: c["id"])
def test_gold_case(case: dict, index: CorpusIndex) -> None:
    intent = build_intent(case["question"], index, use_llm=False)
    _assert_subset(case.get("expect", {}), intent.to_dict())


def test_alias_resolves_to_corp_name(index: CorpusIndex) -> None:
    """조인 키는 항상 corp_name이어야 한다 (raw/ 폴더명 = manifest.corp_name)."""
    pairs = {
        "현대차": "현대자동차",
        "KT": "케이티",
        "엔씨소프트": "NC",
        "삼성화재": "삼성화재해상보험",
        "LS ELECTRIC": "엘에스일렉트릭",
        "LIG넥스원": "LIG디펜스앤에어로스페이스",
        "네이버": "NAVER",
        "포스코": "POSCO홀딩스",
    }
    for spoken, canonical in pairs.items():
        intent = build_intent(f"{spoken}의 2025년 매출액은?", index, use_llm=False)
        assert intent.manifest_filter.corp_names == [canonical], spoken


def test_non_ok_route_clears_filter(index: CorpusIndex) -> None:
    """검색으로 넘기지 않는 질의는 2단계가 실수로 조회하지 못하게 필터를 비운다."""
    for question in ["삼성전자의 2022년 매출액은?", "삼성전자 지금 사도 되나?", "매출액 얼마야?"]:
        intent = build_intent(question, index, use_llm=False)
        assert intent.route != "ok"
        assert intent.manifest_filter.corp_names == []
        assert intent.manifest_filter.doc_group is None
        assert intent.doc_count is None


def test_zero_docs_is_not_unanswerable(index: CorpusIndex) -> None:
    """건수 0 ≠ 결측. 정상 질의는 0건이어도 ok로 넘기고 2단계가 not_found를 판단한다."""
    intent = build_intent("시프트업의 2023년 사업보고서 매출액은?", index, use_llm=False)
    assert intent.route == "ok"
    assert intent.doc_count == 0
    assert intent.warnings


def test_periodic_not_clipped_by_rcept(index: CorpusIndex) -> None:
    """FY2025 사업보고서는 2026년에 접수된다. 보고기간과 접수일을 섞으면 안 된다."""
    intent = build_intent("삼성전자의 2025년 사업보고서 매출액은?", index, use_llm=False)
    assert intent.manifest_filter.base_years == [2025]
    assert intent.manifest_filter.rcept_from is None
    assert intent.doc_count and intent.doc_count > 0


def test_sector_query_does_not_invent_corps(index: CorpusIndex) -> None:
    intent = build_intent("2차전지 기업 중 2025년 설비투자가 가장 큰 곳은?", index, use_llm=False)
    assert intent.manifest_filter.corp_names == []
    assert intent.manifest_filter.sector == "2차전지"
    assert len(intent.sector_members) == 3


def test_guard_does_not_cross_a_token_boundary(index: CorpusIndex) -> None:
    intent = build_intent("삼성전자의 2025년 3분기 사업부문별 매출액은?", index, use_llm=False)
    assert intent.route == "ok"


def test_external_company_in_explicit_counterparty_role_is_not_query_target(index: CorpusIndex) -> None:
    intent = build_intent("삼성전자의 공급계약 계약상대방은 테슬라인가?", index, use_llm=False)
    assert intent.route == "ok"
    assert intent.unknown_entities == []
    assert intent.related_entities == ["테슬라"]


def test_multi_metric_query_keeps_compatibility_fields_and_adds_ordered_plan(index: CorpusIndex) -> None:
    intent = build_intent("삼성전자의 2025년 매출액과 영업이익을 알려줘", index, use_llm=False)
    assert intent.route == "ok"
    assert intent.metric == "operating_profit"
    assert [item["metric"] for item in intent.query_plan] == ["revenue", "operating_profit"]
    assert intent.query_plan[0]["manifest_filter"]["corp_names"] == ["삼성전자"]


def test_multi_report_query_splits_periodic_filters(index: CorpusIndex) -> None:
    intent = build_intent("삼성전자의 2025년 사업보고서와 3분기보고서 매출액은?", index, use_llm=False)
    assert intent.route == "ok"
    assert [item["manifest_filter"]["doc_subtype"] for item in intent.query_plan] == [
        "annual",
        "quarter",
    ]


def test_multi_query_flag_can_roll_back_to_single_query_path(index: CorpusIndex, monkeypatch) -> None:
    monkeypatch.setenv("DIS164_QUERY_PLAN_V1", "false")
    intent = build_intent("삼성전자의 2025년 매출액과 영업이익을 알려줘", index, use_llm=False)
    assert intent.query_plan == []
