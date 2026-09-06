from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.agents.answer import AnswerWriter
from stage3.contracts import Stage3Fact
from stage3.deterministic.normalization import normalize_number
from stage4.numeric import validate_numeric_answer


def _segment_fact(row_label: str, value: int) -> Stage3Fact:
    return Stage3Fact.from_dict(
        {
            "metric": "revenue",
            "label": "매출액",
            "value": value,
            "raw_value": str(value),
            "normalized_value": normalize_number(value, "백만원"),
            "unit": "백만원",
            "period": "2025",
            "basis": "연결",
            "company": "현대자동차",
            "document_id": "20251114002658_14",
            "kind": "numeric",
            "aggregation_scope": "segment",
            "table_context": {
                "row_label": row_label,
                "period_label": "2025년 3분기(제58기)",
            },
        }
    )


def test_segment_fallback_answer_is_numeric_validator_safe() -> None:
    question = "현대자동차 2025년 3분기 사업부문별 매출을 알려주세요."
    intent = adapt_stage1_intent(
        {
            "raw_question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": "lookup",
            "question_type": "lookup",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [9]},
            "companies": ["현대자동차"],
            "aggregation_scope": "segment",
        },
        question=question,
    )
    facts = [
        _segment_fact("차량부문 매출액", 109041330),
        _segment_fact("기타부문 매출액", 7573182),
    ]
    citations = [
        {
            "document_id": "20251114002658_14",
            "source": "dart",
            "evidence": "사업부문별 매출액 표",
        }
    ]

    answer = AnswerWriter._template(intent, facts, [], [], [], citations, [])
    numeric = validate_numeric_answer(
        answer,
        {"facts": [fact.to_dict() for fact in facts]},
        intent,
    )

    assert "2025년 3분기" in answer
    assert "문서ID: 20251114002658_14" in answer
    assert numeric["pass"] is True
    assert numeric["unmatched"] == []
