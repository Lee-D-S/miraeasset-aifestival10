from __future__ import annotations

import pytest

from interpreter.index.corpus_index import CorpusIndex
from interpreter.pipeline.build_intent import build_intent


pytestmark = pytest.mark.needs_corpus


def test_segment_revenue_question_uses_revenue_metric_and_segment_scope():
    index = CorpusIndex.load()
    question = "\ud604\ub300\uc790\ub3d9\ucc28 2025\ub144 3\ubd84\uae30 \uc0ac\uc5c5\ubd80\ubb38\ubcc4 \ub9e4\ucd9c\uc744 \uc54c\ub824\uc8fc\uc138\uc694."
    intent = build_intent(question, index, use_llm=False)

    assert intent.route == "ok"
    assert intent.metric == "revenue"
    assert intent.query_plan == []
    assert intent.manifest_filter.doc_subtype == "quarter"
    assert intent.time.base_months == [9]


@pytest.mark.parametrize(
    ("question", "metric"),
    [
        ("현대자동차 2025년 3분기 사업부문별 영업이익을 알려주세요.", "operating_profit"),
        ("현대자동차 2025년 3분기 사업부문별 설비투자를 알려주세요.", "capex"),
        ("현대자동차 2025년 3분기 세그먼트별 매출액을 알려주세요.", "revenue"),
    ],
)
def test_scoped_financial_questions_do_not_route_to_business_overview(question, metric):
    index = CorpusIndex.load()

    intent = build_intent(question, index, use_llm=False)

    assert intent.route == "ok"
    assert intent.metric == metric
