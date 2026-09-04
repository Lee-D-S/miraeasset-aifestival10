"""Pure helpers for evaluating retrieval results against a gold set."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class GoldQuery:
    """Metadata-only retrieval case used by retrieval backend experiments."""

    id: str
    category: str
    question: str
    manifest_filter: dict[str, Any]
    expected_chunk_ids: tuple[str, ...]
    answerable: bool
    safety_expectation: str | None = None

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "GoldQuery":
        query_id = str(payload.get("id") or "").strip()
        category = str(payload.get("category") or "").strip()
        question = str(payload.get("question") or "").strip()
        if not query_id or not category or not question:
            raise ValueError("gold query requires id, category, and question")
        raw_expected = payload.get("expected_chunk_ids", [])
        if not isinstance(raw_expected, Sequence) or isinstance(raw_expected, (str, bytes)):
            raise ValueError(f"expected_chunk_ids must be a list: {query_id}")
        expected = tuple(
            str(value).strip()
            for value in raw_expected
            if str(value).strip()
        )
        raw_answerable = payload.get("answerable", bool(expected))
        if not isinstance(raw_answerable, bool):
            raise ValueError(f"answerable must be boolean: {query_id}")
        answerable = raw_answerable
        if answerable and not expected:
            raise ValueError(f"answerable gold query has no expected chunks: {query_id}")
        if not answerable and expected:
            raise ValueError(f"unanswerable gold query has expected chunks: {query_id}")
        manifest_filter = payload.get("manifest_filter") or {}
        if not isinstance(manifest_filter, Mapping):
            raise ValueError(f"gold query manifest_filter must be an object: {query_id}")
        return cls(
            id=query_id,
            category=category,
            question=question,
            manifest_filter=dict(manifest_filter),
            expected_chunk_ids=expected,
            answerable=answerable,
            safety_expectation=(
                str(payload["safety_expectation"]).strip()
                if payload.get("safety_expectation") is not None
                else None
            ),
        )


def load_gold_queries(path: str | Path) -> tuple[GoldQuery, ...]:
    """Load and validate the metadata-only gold set."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    raw_queries = payload.get("queries") if isinstance(payload, Mapping) else None
    if not isinstance(raw_queries, list):
        raise ValueError("gold set must contain a queries list")
    queries = tuple(GoldQuery.from_dict(item) for item in raw_queries)
    ids = [query.id for query in queries]
    if len(ids) != len(set(ids)):
        raise ValueError("gold set contains duplicate query IDs")
    return queries


def recall_at_k(expected_ids: Sequence[str], retrieved_ids: Sequence[str], k: int = 20) -> float:
    """Return set recall for expected evidence IDs within the top k."""

    expected = {str(value) for value in expected_ids if str(value)}
    if not expected:
        return 0.0
    retrieved = {str(value) for value in retrieved_ids[:k]}
    return len(expected & retrieved) / len(expected)


def reciprocal_rank(expected_ids: Sequence[str], retrieved_ids: Sequence[str], k: int = 20) -> float:
    """Return the reciprocal rank of the first expected evidence ID."""

    expected = {str(value) for value in expected_ids if str(value)}
    for index, identifier in enumerate(retrieved_ids[:k], start=1):
        if str(identifier) in expected:
            return 1.0 / index
    return 0.0


def summarize_results(
    queries: Sequence[GoldQuery],
    results: Mapping[str, Sequence[str]],
    *,
    top_k: int = 20,
) -> dict[str, Any]:
    """Aggregate retrieval scores overall and by query category."""

    rows = [
        query
        for query in queries
        if query.answerable and query.expected_chunk_ids
    ]
    by_category: dict[str, list[dict[str, float]]] = defaultdict(list)
    for query in rows:
        retrieved = results.get(query.id, ())
        by_category[query.category].append(
            {
                "recall_at_k": recall_at_k(query.expected_chunk_ids, retrieved, top_k),
                "mrr": reciprocal_rank(query.expected_chunk_ids, retrieved, top_k),
            }
        )

    def average(items: Sequence[Mapping[str, float]], key: str) -> float:
        return sum(float(item[key]) for item in items) / len(items) if items else 0.0

    category_summary = {
        category: {
            "query_count": len(items),
            "recall_at_k": average(items, "recall_at_k"),
            "mrr": average(items, "mrr"),
        }
        for category, items in sorted(by_category.items())
    }
    all_items = [item for items in by_category.values() for item in items]
    return {
        "query_count": len(all_items),
        "top_k": top_k,
        "recall_at_k": average(all_items, "recall_at_k"),
        "mrr": average(all_items, "mrr"),
        "by_category": category_summary,
    }


def compare_summaries(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> dict[str, Any]:
    """Return candidate-minus-baseline deltas and the adoption gates."""

    categories = sorted(
        set((baseline.get("by_category") or {}))
        | set((candidate.get("by_category") or {}))
    )
    by_category = {}
    no_category_regression = True
    for category in categories:
        before = (baseline.get("by_category") or {}).get(category, {})
        after = (candidate.get("by_category") or {}).get(category, {})
        recall_delta = float(after.get("recall_at_k", 0.0)) - float(before.get("recall_at_k", 0.0))
        mrr_delta = float(after.get("mrr", 0.0)) - float(before.get("mrr", 0.0))
        by_category[category] = {
            "baseline_recall_at_k": float(before.get("recall_at_k", 0.0)),
            "candidate_recall_at_k": float(after.get("recall_at_k", 0.0)),
            "recall_delta": recall_delta,
            "baseline_mrr": float(before.get("mrr", 0.0)),
            "candidate_mrr": float(after.get("mrr", 0.0)),
            "mrr_delta": mrr_delta,
        }
        no_category_regression = no_category_regression and recall_delta >= 0.0
    overall_delta = float(candidate.get("recall_at_k", 0.0)) - float(baseline.get("recall_at_k", 0.0))
    return {
        "overall_recall_delta": overall_delta,
        "overall_mrr_delta": float(candidate.get("mrr", 0.0)) - float(baseline.get("mrr", 0.0)),
        "by_category": by_category,
        "passes_category_no_regression": no_category_regression,
        "passes_minimum_improvement": overall_delta >= 0.05,
        "adopt_prefix": no_category_regression and overall_delta >= 0.05,
    }


__all__ = [
    "GoldQuery",
    "compare_summaries",
    "load_gold_queries",
    "recall_at_k",
    "reciprocal_rank",
    "summarize_results",
]
