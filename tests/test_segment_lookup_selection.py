from stage3.agents.answer import _segment_lookup_facts
from stage3.contracts import Stage3Fact, Stage3Intent


def _fact(*, kind: str, value: str, row_label: str = "") -> Stage3Fact:
    return Stage3Fact(
        metric="revenue",
        label="매출액",
        value=value,
        raw_value=value,
        unit="백만원",
        normalized_value=value,
        period="2025-09",
        basis="연결",
        company="현대자동차",
        document_id="doc",
        source="source",
        evidence=value,
        kind=kind,
        table_context={"row_label": row_label} if row_label else {},
        aggregation_scope="segment",
    )


def test_segment_lookup_prefers_table_numeric_facts_over_narrative_numbers() -> None:
    intent = Stage3Intent(
        question="현대자동차 2025년 3분기 사업부문별 매출액은?",
        normalized_question="현대자동차 2025년 3분기 사업부문별 매출액은?",
        route="ok",
        intent="lookup",
        question_type="lookup",
        metric="revenue",
        companies=["현대자동차"],
        time={"years": [2025]},
        basis="연결",
    )
    facts = [
        _fact(kind="text", value="4조 2,134억원", row_label="기타부문"),
        _fact(kind="numeric", value="109041330", row_label="차량부문 매출액"),
    ]

    selected = _segment_lookup_facts(intent, facts)

    assert [fact.value for fact in selected] == ["109041330"]
