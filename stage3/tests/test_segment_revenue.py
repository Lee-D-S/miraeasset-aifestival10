from __future__ import annotations

import re

from stage3.agents.answer import AnswerWriter
from stage3.agents.fact_extraction import _extract_facts_uncached
from stage3.contracts import Stage3Document, Stage3Fact, adapt_stage1_intent


QUESTION = "\ud604\ub300\uc790\ub3d9\ucc28 2025\ub144 3\ubd84\uae30 \uc0ac\uc5c5\ubd80\ubb38\ubcc4 \ub9e4\ucd9c\uc744 \uc54c\ub824\uc8fc\uc138\uc694."


def _intent():
    return adapt_stage1_intent(
        {
            "metric": "revenue", "intent": "lookup", "question_type": "lookup",
            "question": QUESTION, "companies": ["\ud604\ub300\uc790\ub3d9\ucc28"], "basis": "\uc5f0\uacb0",
            "time": {"years": [2025], "base_months": [9]},
        },
        question=QUESTION,
    )


def _segment_facts() -> list[Stage3Fact]:
    return [
        Stage3Fact(
            metric="revenue", label="\ub9e4\ucd9c\uc561", value=value, raw_value=str(value),
            unit="\ubc31\ub9cc\uc6d0", normalized_value=value, period="2025",
            basis="\uc5f0\uacb0", company="\ud604\ub300\uc790\ub3d9\ucc28", document_id=document_id,
            source="20251114002658.xml", evidence=row_label, table_context={"row_label": row_label},
            aggregation_scope="segment",
        )
        for row_label, value, document_id in [
            ("\ucc28\ub7c9\ubd80\ubb38 \ub9e4\ucd9c\uc561", 109041330, "vehicle"),
            ("\uae30\ud0c0\ubd80\ubb38 \ub9e4\ucd9c\uc561", 7573182, "other"),
        ]
    ]


def test_segment_table_extracts_amounts_and_excludes_ratio_cells():
    raw = (
        "<TABLE>"
        "<TR><TH>구분</TH><TH>구분</TH><TH>2025년 3분기</TH><TH>2025년 3분기</TH></TR>"
        "<TR><TH>구분</TH><TH>구분</TH><TH>금액</TH><TH>비중</TH></TR>"
        "<TR><TD>차량부문</TD><TD>매출액</TD><TD>109,041,330</TD><TD>78.2</TD></TR>"
        "<TR><TD>기타부문</TD><TD>매출액</TD><TD>7,573,182</TD><TD>5.4</TD></TR>"
        "</TABLE>"
    )
    facts = _extract_facts_uncached(
        [Stage3Document(
            id="segment-doc", source="segment.xml", text=raw,
            metadata={"corp_name": "현대자동차", "base_year": 2025, "base_month": 9, "basis": "연결"},
        )],
        _intent(),
    )
    amounts = [fact for fact in facts if fact.metric == "revenue" and fact.unit != "%"]
    assert {(fact.value, fact.table_context["row_label"]) for fact in amounts} == {
        (109041330.0, "차량부문 매출액"),
        (7573182.0, "기타부문 매출액"),
    }


def test_segment_answer_lists_each_business_segment_amount():
    answer, _mode = AnswerWriter().write(
        question=QUESTION, intent=_intent(), facts=_segment_facts(), calculations=[],
        comparisons=[], events=[], citations=[], warnings=[],
    )
    digits = re.sub(r"\D", "", answer)
    assert "109041330" in digits
    assert "7573182" in digits
    assert "차량부문 매출액" in answer
    assert "기타부문 매출액" in answer
