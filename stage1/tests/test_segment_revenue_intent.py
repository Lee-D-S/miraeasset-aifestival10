from __future__ import annotations

import pytest

from stage1.index.corpus_index import CorpusIndex
from stage1.pipeline.build_intent import build_intent


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
