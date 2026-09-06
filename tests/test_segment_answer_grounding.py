from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.agents.answer import AnswerWriter
from reasoner.contracts import ReasonerFact
from reasoner.deterministic.normalization import normalize_number
from validator.numeric import validate_numeric_answer


def _segment_fact(row_label: str, value: int) -> ReasonerFact:
    return ReasonerFact.from_dict(
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
    intent = adapt_interpreter_intent(
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


def test_template_limitations_drop_retrieval_trace_lines() -> None:
    question = "삼성전자의 최근 3년 매출액 추이를 알려줘"
    intent = adapt_interpreter_intent(
        {
            "raw_question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2023, 2024, 2025], "base_months": [12]},
            "companies": ["삼성전자"],
        },
        question=question,
    )
    calculation = {
        "status": "ok",
        "operation": "percentage_change",
        "formula": "(new-old)/abs(old)*100",
        "result": 28.8,
        "unit": "%",
        "inputs": [
            {"period": "2023-12", "value": "258조", "unit": ""},
            {"period": "2025-12", "value": "333조", "unit": ""},
        ],
    }
    warnings = [
        "revenue:query=삼성전자의 최근 3년 매출액 추이를 알려줘 연결 2023 2024 2025",
        "revenue:candidate_count=999",
        "revenue:keyword_count=100",
        "revenue:reranker=deterministic",
        "revenue:merged_count=200",
        "2023-12 값을 확인할 수 없습니다.",
    ]
    facts = [_segment_fact("매출액", 258935500)]
    answer = AnswerWriter._template(intent, facts, [calculation], [], [], [], warnings)

    assert "candidate_count" not in answer
    assert "reranker=" not in answer
    assert "query=" not in answer
    assert "정보 한계\n- 2023-12 값을 확인할 수 없습니다." in answer
