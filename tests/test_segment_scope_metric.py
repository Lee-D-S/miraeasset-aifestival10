from types import SimpleNamespace

import json

from interpreter.index.corpus_index import squash
from interpreter.pipeline.slot_extractor import SlotResult, _extract_metric


def test_scoped_metric_overrides_business_overview_label():
    with open("interpreter/config/metric_router.json", encoding="utf-8") as handle:
        config = SimpleNamespace(metrics=json.load(handle))
    index = SimpleNamespace(config=config)

    cases = {
        "현대자동차 2025년 3분기 사업부문별 영업이익": "operating_profit",
        "현대자동차 2025년 3분기 사업부문별 설비투자": "capex",
        "현대자동차 2025년 3분기 세그먼트별 매출액": "revenue",
    }
    for question, expected_metric in cases.items():
        slots = SlotResult()
        _extract_metric(squash(question), slots, index)
        assert slots.metric == expected_metric
        assert slots.metric_matches == [expected_metric]
