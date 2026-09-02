from __future__ import annotations

import pytest

from scripts.compare_query_instruction import _safety_result, _validate_gold_set
from stage2.query_instruction_eval import (
    GoldQuery,
    compare_summaries,
    recall_at_k,
    reciprocal_rank,
    summarize_results,
)


def _query(query_id: str, category: str, *expected: str) -> GoldQuery:
    return GoldQuery(
        id=query_id,
        category=category,
        question="질문",
        manifest_filter={},
        expected_chunk_ids=tuple(expected),
        answerable=True,
    )


def test_retrieval_metrics_support_multiple_expected_chunks():
    assert recall_at_k(["a", "b"], ["x", "b", "a"], 2) == 0.5
    assert reciprocal_rank(["a", "b"], ["x", "b", "a"], 2) == 0.5


def test_summary_and_adoption_gate_are_category_aware():
    queries = (_query("q1", "lookup", "a"), _query("q2", "compare", "b"))
    baseline = summarize_results(queries, {"q1": ["a"], "q2": ["x"]})
    candidate = summarize_results(queries, {"q1": ["a"], "q2": ["b"]})
    comparison = compare_summaries(baseline, candidate)
    assert candidate["recall_at_k"] == 1.0
    assert comparison["passes_category_no_regression"] is True
    assert comparison["passes_minimum_improvement"] is True
    assert comparison["adopt_prefix"] is True


def test_supplied_gold_set_has_the_planned_shape():
    from stage2.query_instruction_eval import load_gold_queries

    queries = load_gold_queries(
        "tests/fixtures/e5_query_instruction_gold.json"
    )
    _validate_gold_set(queries)


def test_gold_validation_rejects_a_missing_category():
    query = _query("q1", "single_lookup", "a")
    with pytest.raises(ValueError, match="25 queries"):
        _validate_gold_set((query,))


def test_information_limit_safety_gate_rejects_grounded_numeric_success():
    unsafe = _safety_result(
        {
            "route": "ok",
            "intent": {"metric": "revenue", "question_type": "lookup"},
            "stage3_result": {"status": "success"},
            "stage4_result": {
                "status": "success",
                "numeric_check": {"pass": True},
                "semantic_check": {"unsupported_claims": []},
            },
        }
    )
    assert unsafe["safe"] is False


def test_information_limit_safety_gate_accepts_blocked_response():
    safe = _safety_result(
        {
            "route": "unanswerable",
            "stage3_result": {"status": "unanswerable"},
            "stage4_result": {
                "status": "unanswerable",
                "numeric_check": {},
                "semantic_check": {"unsupported_claims": []},
            },
        }
    )
    assert safe["safe"] is True
