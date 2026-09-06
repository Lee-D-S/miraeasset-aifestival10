"""Deterministic expansion of a multi-metric or multi-report question."""

from __future__ import annotations

from dataclasses import asdict
import os
from typing import Any

from ..index.corpus_index import CorpusIndex, squash
from ..models.intent import ManifestFilter
from .slot_extractor import SlotResult


def _metric_spec(index: CorpusIndex, metric: str) -> dict[str, Any]:
    for spec in index.config.metrics.get("metrics", []):
        if spec.get("key") == metric:
            return dict(spec)
    return {}


def _explicit_periodic_subtypes(question: str, index: CorpusIndex) -> list[str]:
    compact = squash(question)
    matches: list[tuple[int, str]] = []
    for entry in index.config.metrics.get("doc_keywords", []):
        subtype = entry.get("doc_subtype")
        if entry.get("doc_group") != "periodic" or not subtype:
            continue
        for label in entry.get("labels", []):
            key = squash(label)
            position = compact.find(key)
            if key and position >= 0:
                matches.append((position, str(subtype)))
                break
    ordered: list[str] = []
    for _position, subtype in sorted(matches):
        if subtype not in ordered:
            ordered.append(subtype)
    return ordered


def _filter_for_subquery(
    base_filter: ManifestFilter,
    *,
    metric_spec: dict[str, Any],
    subtype: str | None,
    index: CorpusIndex,
) -> dict[str, Any]:
    values = asdict(base_filter)
    metric_group = metric_spec.get("doc_group")
    if metric_group:
        values["doc_group"] = metric_group
        values["doc_group_candidates"] = []
    if metric_spec.get("report_nm_contains"):
        values["report_nm_contains"] = list(metric_spec["report_nm_contains"])

    if subtype and (not metric_group or metric_group == "periodic"):
        values["doc_group"] = "periodic"
        values["doc_group_candidates"] = []
        values["doc_subtype"] = subtype
        values["doc_subtype_candidates"] = []
        months = index.config.bounds.get("base_month_by_subtype", {}).get(subtype, [])
        values["base_months"] = [int(months)] if isinstance(months, int) else list(months)
        values["rcept_from"] = None
        values["rcept_to"] = None
    elif metric_spec.get("doc_subtype"):
        values["doc_subtype"] = metric_spec["doc_subtype"]
        values["doc_subtype_candidates"] = []
    return values


def build_query_plan(
    question: str,
    slots: SlotResult,
    base_filter: ManifestFilter,
    index: CorpusIndex,
) -> list[dict[str, Any]]:
    """Return subqueries only when the question has independent dimensions.

    A ratio such as ``영업이익 대비 연구개발비 비중`` intentionally keeps its
    two metrics in one calculation and is therefore not split here.
    """

    if not query_plan_enabled():
        return []

    metrics = list(dict.fromkeys(slots.metric_matches))
    operation = str(slots.calculation.get("operation") or "")
    split_metrics = len(metrics) > 1 and operation not in {"ratio_percent", "margin"}
    report_subtypes = _explicit_periodic_subtypes(question, index)
    if not report_subtypes and slots.doc_subtype:
        report_subtypes = [slots.doc_subtype]
    split_reports = len(report_subtypes) > 1
    if not split_metrics and not split_reports:
        return []

    metric_values = metrics if split_metrics else [slots.metric]
    metric_values = [metric for metric in metric_values if metric]
    subtype_values = report_subtypes if split_reports else [None]
    plan: list[dict[str, Any]] = []
    for metric in metric_values:
        spec = _metric_spec(index, str(metric))
        for subtype in subtype_values:
            subquery_id = f"subquery-{len(plan) + 1}"
            calculation = dict(slots.calculation)
            if split_metrics and calculation.get("metric") == slots.metric:
                calculation["metric"] = metric
            sub_filter = _filter_for_subquery(
                base_filter,
                metric_spec=spec,
                subtype=subtype,
                index=index,
            )
            plan.append(
                {
                    "subquery_id": subquery_id,
                    "metric": metric,
                    "question_type": slots.question_type,
                    "calculation": calculation,
                    "basis": slots.basis,
                    "time": {
                        "mode": slots.time_mode,
                        "years": list(slots.years),
                        "doc_subtype": subtype or slots.doc_subtype,
                        "base_months": list(
                            _filter_for_subquery(
                                base_filter,
                                metric_spec=spec,
                                subtype=subtype,
                                index=index,
                            )["base_months"]
                        ),
                        "prefer_latest": slots.prefer_latest,
                        "relative_terms": list(slots.relative_terms),
                    },
                    "manifest_filter": sub_filter,
                }
            )
    return plan


def query_plan_enabled() -> bool:
    value = os.getenv("DIS164_QUERY_PLAN_V1", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}


__all__ = ["build_query_plan", "query_plan_enabled"]
