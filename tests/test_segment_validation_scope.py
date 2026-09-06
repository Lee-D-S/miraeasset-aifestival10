from stage3.contracts import Stage3Fact
from stage4.node import (
    _missing_segment_facts,
    _segment_facts_for_validation,
    build_stage4_node,
)


def _fact(value: int, *, table: bool, row_label: str = "") -> Stage3Fact:
    return Stage3Fact.from_dict(
        {
            "kind": "numeric",
            "metric": "revenue",
            "label": "revenue",
            "value": value,
            "raw_value": str(value),
            "normalized_value": value,
            "unit": "M",
            "period": "2025",
            "basis": "consolidated",
            "company": "Company A",
            "document_id": "doc-1",
            "aggregation_scope": "segment",
            "table_context": {"row_label": row_label} if table else {},
        }
    )


def test_segment_validation_uses_all_table_rows_for_any_cardinality() -> None:
    facts = [
        _fact(4_213_400_000_000, table=False),
        _fact(109_041_330, table=True, row_label="vehicle"),
        _fact(7_573_182, table=True, row_label="other"),
        _fact(12_345, table=True, row_label="service"),
    ]
    answer = "vehicle 109041330M; other 7573182M; service 12345M"

    assert _missing_segment_facts(answer, facts) == ["revenue"]
    assert _missing_segment_facts(answer, _segment_facts_for_validation(facts)) == []


def test_segment_validation_falls_back_to_all_amounts_without_table_context() -> None:
    facts = [_fact(10, table=False), _fact(20, table=False)]

    assert _segment_facts_for_validation(facts) == facts


class _PassingSemanticClient:
    def generate_json(self, _messages, *, schema):
        return {
            "pass": True,
            "issues": [],
            "unsupported_claims": [],
            "missing_aspects": [],
            "summary": "ok",
        }


def test_stage4_accepts_breakdown_without_unrelated_narrative_total() -> None:
    facts = [
        _fact(4_213_400_000_000, table=False),
        _fact(109_041_330, table=True, row_label="vehicle"),
        _fact(7_573_182, table=True, row_label="other"),
    ]
    stage3_result = {
        "status": "success",
        "answer": "vehicle 109041330; other 7573182 [source:doc-1]",
        "facts": [fact.to_dict() for fact in facts],
        "citations": [{"document_id": "doc-1", "source": "source", "evidence": "evidence"}],
    }
    update = build_stage4_node(validator_client=_PassingSemanticClient())(
        {
            "route": "ok",
            "question": "Company A 2025 segment revenue",
            "intent": {
                "question_type": "lookup",
                "metric": "revenue",
                "companies": ["Company A"],
                "basis": "consolidated",
                "time": {"years": [2025], "base_months": [9]},
            },
            "answer": stage3_result["answer"],
            "stage3_result": stage3_result,
        }
    )

    assert update["stage4_result"]["status"] == "success"
