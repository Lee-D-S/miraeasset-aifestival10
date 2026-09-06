"""Canonical calculation-plan compiler and validator.

Interpreter still extracts a small calculation seed and ``query_plan`` still
describes independent retrieval units.  This module is the only active
boundary that turns those seeds into an executable ``analysis_plan``.  The
plan is data-only: values and document ids can only come from grounded Facts
at execution time.
"""

from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any, Protocol

from reasoner.contracts import ReasonerIntent
from reasoner.metric_registry import METRIC_SPECS


SUPPORTED_OPERATIONS = frozenset({
    "add", "subtract", "multiply", "divide", "percentage_change", "cagr",
    "ratio_percent", "margin", "sum", "average", "min", "max", "rank",
    "percentage_point_change",
})

PLAN_SCHEMA_VERSION = 1
MAX_PLAN_NODES = 32
MAX_PLAN_DEPTH = 8
MAX_PLAN_MAPS = 1
MAX_PLAN_REDUCES = 1
NUMERIC_METRICS = frozenset(
    metric
    for metric, spec in METRIC_SPECS.items()
    if spec.get("numeric_labels")
)
_GROUP_FIELDS = frozenset({"company", "period"})


def _numeric_metric_catalog() -> str:
    """List every registered numeric metric with its Korean labels.

    Fed into the LLM planner's prompt so it picks numerator/denominator
    metric ids from the same registry `validate_analysis_plan` checks
    against, instead of guessing a name that gets rejected.
    """

    return ", ".join(
        f"{metric}({'/'.join(METRIC_SPECS[metric].get('numeric_labels', ()))})"
        for metric in sorted(NUMERIC_METRICS)
    )


class PlanProposalProvider(Protocol):
    """Minimal provider contract for an optional structured LLM proposal."""

    def generate_json(self, messages: list[dict[str, Any]], *, schema: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]: ...


ANALYSIS_PLAN_SCHEMA: dict[str, Any] = {
    "schema_version": PLAN_SCHEMA_VERSION,
    "status": "ready",
    "capability": "calculation",
    "requirements": [
        {
            "id": "metric-id",
            "metric": "registered numeric metric",
            "role": "input",
            "companies": ["company"],
            "periods": ["YYYY"],
            "required": True,
            "manifest_filter": {},
        }
    ],
    "steps": [
        {
            "id": "step-id",
            "operation": "allow-listed operation",
            "inputs": ["requirement-or-prior-step-id"],
            "group_by": ["company", "period"],
            "period_relation": "year_over_year",
        }
    ],
    "output": {"ref": "step-id", "unit": "", "precision": 2},
    "assumptions": [],
}


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).lower()


def _requested_periods(intent: ReasonerIntent, question: str) -> list[str]:
    raw_time = intent.time if isinstance(intent.time, Mapping) else {}
    if raw_time.get("mode") == "disclosure":
        return []
    years: list[int] = []
    for value in raw_time.get("years", []):
        try:
            year = int(value)
        except (TypeError, ValueError):
            continue
        if year not in years:
            years.append(year)
    compact = _compact(question)
    relative_terms = " ".join(str(value) for value in raw_time.get("relative_terms", []))
    has_previous = bool(
        "전년" in _compact(relative_terms)
        or any(term in compact for term in ("전년대비", "전년보다", "지난해대비", "이전연도대비"))
    )
    if has_previous and len(years) == 1 and years[0] > 1900:
        years.insert(0, years[0] - 1)
    return [str(year) for year in years]


def _companies(intent: ReasonerIntent) -> list[str]:
    values = list(intent.companies or intent.sector_members)
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _metric_candidates(intent: ReasonerIntent, question: str) -> list[str]:
    values: list[str] = []
    for item in intent.query_plan:
        if isinstance(item, Mapping) and item.get("metric"):
            values.append(str(item["metric"]))
    if intent.metric:
        values.append(str(intent.metric))
    source_matches = intent.source.get("metric_matches") if isinstance(intent.source, Mapping) else None
    if isinstance(source_matches, (list, tuple)):
        values.extend(str(value) for value in source_matches if str(value).strip())

    compact = _compact(question)
    positions: list[tuple[int, str]] = []
    for metric, spec in METRIC_SPECS.items():
        if metric not in NUMERIC_METRICS:
            continue
        found = [
            compact.find(_compact(label))
            for label in spec.get("numeric_labels", ())
            if _compact(label) and compact.find(_compact(label)) >= 0
        ]
        if found:
            positions.append((min(found), metric))
    values.extend(metric for _position, metric in sorted(positions))
    return list(dict.fromkeys(value for value in values if value in NUMERIC_METRICS))


def _ratio_metrics(intent: ReasonerIntent, question: str, candidates: list[str]) -> tuple[str | None, str | None]:
    compact = _compact(question)
    marker = compact.find("대비")
    positioned: list[tuple[int, str]] = []
    for metric, spec in METRIC_SPECS.items():
        if metric not in NUMERIC_METRICS:
            continue
        for label in spec.get("numeric_labels", ()):
            alias = _compact(label)
            position = compact.find(alias)
            if alias and position >= 0:
                positioned.append((position, metric))
                break
    before = [metric for position, metric in sorted(positioned) if marker >= 0 and position < marker]
    after = [metric for position, metric in sorted(positioned) if marker >= 0 and position > marker]
    denominator = before[-1] if before else None
    numerator = after[0] if after else None
    if numerator and denominator and numerator != denominator:
        return numerator, denominator
    numerator = str(intent.metric or "") if intent.metric in NUMERIC_METRICS else (candidates[0] if candidates else None)
    calculation = intent.calculation if isinstance(intent.calculation, Mapping) else {}
    denominator = str(calculation.get("denominator_metric") or "")
    # A denominator that collapses onto the same metric as the numerator
    # (e.g. the requested numerator has no numeric_labels entry, so the
    # fallback above picked the denominator's own metric as numerator too)
    # must not be returned as-is: the caller would build a plan with two
    # same-id requirements and validate_analysis_plan would raise.
    if denominator not in NUMERIC_METRICS or denominator == numerator:
        denominator = "revenue" if numerator and numerator != "revenue" else None
    return numerator, denominator


def _manifest_filter(intent: ReasonerIntent, *, companies: list[str], periods: list[str]) -> dict[str, Any]:
    manifest = dict(intent.manifest_filter or {})
    if companies:
        manifest["corp_names"] = list(companies)
    years = [int(period) for period in periods if str(period).isdigit()]
    if years:
        manifest["base_years"] = years
    return manifest


def _requirement(
    intent: ReasonerIntent,
    *,
    metric: str,
    role: str,
    companies: list[str],
    periods: list[str],
    manifest_filter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": metric,
        "metric": metric,
        "role": role,
        "companies": list(companies),
        "periods": list(periods),
        "required": True,
        "manifest_filter": dict(manifest_filter or _manifest_filter(intent, companies=companies, periods=periods)),
    }


def _single_plan(
    intent: ReasonerIntent,
    *,
    question: str,
    operation: str,
    metric: str | None,
    denominator_metric: str | None = None,
) -> dict[str, Any] | None:
    if operation not in SUPPORTED_OPERATIONS or not metric or metric not in NUMERIC_METRICS:
        return None
    companies = _companies(intent)
    periods = _requested_periods(intent, question)
    requirements = [
        _requirement(
            intent,
            metric=metric,
            role="numerator" if operation in {"ratio_percent", "margin"} else "input",
            companies=companies,
            periods=periods,
        )
    ]
    inputs = [metric]
    if operation in {"ratio_percent", "margin"}:
        if not denominator_metric or denominator_metric not in NUMERIC_METRICS:
            return None
        requirements.append(
            _requirement(intent, metric=denominator_metric, role="denominator", companies=companies, periods=periods)
        )
        inputs.append(denominator_metric)
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "ready",
        "capability": "calculation",
        "requirements": requirements,
        "steps": [{"id": "calculate", "operation": operation, "inputs": inputs, "group_by": ["company", "period"]}],
        "output": {"ref": "calculate", "unit": "%" if operation in {"ratio_percent", "margin", "percentage_change", "cagr"} else "", "precision": 2},
        "assumptions": list(intent.assumptions),
    }


def _complex_ratio_plan(intent: ReasonerIntent, question: str, candidates: list[str]) -> dict[str, Any] | None:
    compact = _compact(question)
    if not any(term in compact for term in ("비중", "비율")):
        return None
    numerator, denominator = _ratio_metrics(intent, question, candidates)
    if not numerator or not denominator or numerator == denominator:
        return None
    has_year_change = any(term in compact for term in ("전년대비", "전년보다", "지난해대비", "증가", "감소", "변화", "증감"))
    has_company_rank = (
        len(_companies(intent)) >= 2
        or any(term in compact for term in ("중", "가장", "최대", "최소"))
        or str(intent.question_type or "").lower() in {"compare", "comparison"}
    )
    if not (has_year_change and has_company_rank):
        return None
    companies = _companies(intent)
    periods = _requested_periods(intent, question)
    if len(periods) < 2:
        return None
    requirements = [
        _requirement(intent, metric=numerator, role="numerator", companies=companies, periods=periods),
        _requirement(intent, metric=denominator, role="denominator", companies=companies, periods=periods),
    ]
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "ready",
        "capability": "calculation",
        "requirements": requirements,
        "steps": [
            {"id": "ratio", "operation": "ratio_percent", "inputs": [numerator, denominator], "group_by": ["company", "period"]},
            {"id": "change", "operation": "percentage_point_change", "inputs": ["ratio"], "period_relation": "year_over_year", "group_by": ["company"]},
            {"id": "rank", "operation": "rank", "inputs": ["change"], "group_by": ["company"]},
        ],
        "output": {"ref": "rank", "unit": "percentage_points", "precision": 2},
        "assumptions": list(intent.assumptions),
    }


def validate_analysis_plan(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and normalize an untrusted deterministic/LLM plan."""

    if not isinstance(value, Mapping):
        raise ValueError("analysis_plan은 mapping이어야 합니다.")
    try:
        version = int(value.get("schema_version", PLAN_SCHEMA_VERSION))
    except (TypeError, ValueError) as error:
        raise ValueError("analysis_plan schema_version이 잘못되었습니다.") from error
    if version != PLAN_SCHEMA_VERSION:
        raise ValueError("지원하지 않는 analysis_plan schema_version입니다.")
    if str(value.get("status", "ready")) != "ready":
        raise ValueError("준비되지 않은 analysis_plan입니다.")
    if str(value.get("capability", "calculation")) != "calculation":
        raise ValueError("현재는 calculation capability만 허용합니다.")

    raw_requirements = value.get("requirements")
    raw_steps = value.get("steps")
    if not isinstance(raw_requirements, list) or not raw_requirements:
        raise ValueError("analysis_plan requirements가 없습니다.")
    if not isinstance(raw_steps, list) or not raw_steps or len(raw_steps) > MAX_PLAN_NODES:
        raise ValueError("analysis_plan steps가 비어 있거나 한도를 초과했습니다.")

    requirements: list[dict[str, Any]] = []
    refs: set[str] = set()
    for raw in raw_requirements:
        if not isinstance(raw, Mapping):
            raise ValueError("analysis_plan requirement 형식이 잘못되었습니다.")
        identifier = str(raw.get("id", "")).strip()
        metric = str(raw.get("metric", "")).strip()
        if not identifier or identifier in refs:
            raise ValueError("analysis_plan requirement id가 중복되거나 비어 있습니다.")
        if metric not in NUMERIC_METRICS:
            raise ValueError(f"등록되지 않은 numeric metric입니다: {metric}")
        companies = [str(item) for item in raw.get("companies", []) if str(item).strip()]
        periods = [str(item) for item in raw.get("periods", []) if str(item).strip()]
        manifest_filter = raw.get("manifest_filter", {})
        if not isinstance(manifest_filter, Mapping):
            raise ValueError(f"requirement manifest_filter가 잘못되었습니다: {identifier}")
        requirements.append({
            "id": identifier,
            "metric": metric,
            "role": str(raw.get("role", "input")),
            "companies": companies,
            "periods": periods,
            "required": bool(raw.get("required", True)),
            "manifest_filter": dict(manifest_filter),
        })
        refs.add(identifier)

    steps: list[dict[str, Any]] = []
    step_refs: set[str] = set()
    depths: dict[str, int] = {}
    map_count = 0
    reduce_count = 0
    for raw in raw_steps:
        if not isinstance(raw, Mapping):
            raise ValueError("analysis_plan step 형식이 잘못되었습니다.")
        identifier = str(raw.get("id", "")).strip()
        operation = str(raw.get("operation", "")).strip()
        if not identifier or identifier in refs or identifier in step_refs:
            raise ValueError("analysis_plan step id가 중복되거나 비어 있습니다.")
        if operation not in SUPPORTED_OPERATIONS:
            raise ValueError(f"지원하지 않는 analysis_plan operation입니다: {operation}")
        raw_inputs = raw.get("inputs")
        if raw_inputs is None and raw.get("input") is not None:
            raw_inputs = [raw.get("input")]
        if not isinstance(raw_inputs, list) or not raw_inputs:
            raise ValueError(f"step inputs가 없습니다: {identifier}")
        inputs = [str(item).strip() for item in raw_inputs if str(item).strip()]
        if len(inputs) != len(raw_inputs) or any(item not in refs and item not in step_refs for item in inputs):
            raise ValueError(f"존재하지 않는 step 입력 참조입니다: {identifier}")
        group_by = [str(item) for item in raw.get("group_by", []) if str(item) in _GROUP_FIELDS]
        if operation == "rank":
            reduce_count += 1
        if operation in {"ratio_percent", "margin", "percentage_point_change"} and len(inputs) > 1:
            map_count += 1
        if map_count > MAX_PLAN_MAPS or reduce_count > MAX_PLAN_REDUCES:
            raise ValueError("analysis_plan map/reduce 한도를 초과했습니다.")
        depth = 1 + max((depths.get(item, 0) for item in inputs), default=0)
        if depth > MAX_PLAN_DEPTH:
            raise ValueError("analysis_plan 깊이를 초과했습니다.")
        depths[identifier] = depth
        step_refs.add(identifier)
        steps.append({
            "id": identifier,
            "operation": operation,
            "inputs": inputs,
            "group_by": group_by,
            **({"period_relation": str(raw["period_relation"])} if raw.get("period_relation") else {}),
        })

    output = value.get("output", {})
    if not isinstance(output, Mapping):
        raise ValueError("analysis_plan output이 잘못되었습니다.")
    output_ref = str(output.get("ref", "")).strip()
    if output_ref not in step_refs:
        raise ValueError("analysis_plan output ref가 step을 가리키지 않습니다.")
    assumptions = [str(item) for item in value.get("assumptions", []) if str(item).strip()]
    return {
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "ready",
        "capability": "calculation",
        "requirements": requirements,
        "steps": steps,
        "output": {
            "ref": output_ref,
            "unit": str(output.get("unit", "")),
            "precision": max(int(output.get("precision", 2)), 0),
        },
        "assumptions": assumptions,
    }


def build_calculation_plan(intent: ReasonerIntent) -> dict[str, Any] | None:
    """Keep the old single-operation adapter for compatibility callers."""

    question_type = (intent.question_type or intent.intent or "").strip().lower()
    if question_type in {"compare", "comparison"}:
        calculation = dict(intent.calculation)
        calculation.setdefault("operation", "rank")
        calculation.setdefault("metric", intent.metric or "")
        return calculation
    calculation = dict(intent.calculation)
    operation = str(calculation.get("operation", "")).strip()
    if not operation:
        return None
    calculation.setdefault("metric", intent.metric or "")
    return calculation


def build_analysis_plan(intent: ReasonerIntent, *, question: str | None = None) -> dict[str, Any] | None:
    """Compile an active Reasoner intent into one validated canonical plan."""

    raw_question = question or intent.question or intent.normalized_question
    existing = intent.analysis_plan
    if isinstance(existing, Mapping) and existing.get("status") == "ready":
        return validate_analysis_plan(existing)

    calculation = build_calculation_plan(intent) or {}
    operation = str(calculation.get("operation", "")).strip()
    candidates = _metric_candidates(intent, raw_question)
    compact = _compact(raw_question)
    ratio_plan = _complex_ratio_plan(intent, raw_question, candidates)
    if ratio_plan is not None:
        return validate_analysis_plan(ratio_plan)

    if not operation:
        if str(intent.question_type or "").lower() in {"compare", "comparison"}:
            operation = "rank"
        elif any(term in compact for term in ("연평균성장률", "cagr")):
            operation = "cagr"
        elif any(term in compact for term in ("증가율", "감소율", "증감", "전년대비")):
            operation = "percentage_change"
        elif any(term in compact for term in ("비중", "비율")):
            operation = "ratio_percent"
    metric = str(calculation.get("metric") or intent.metric or (candidates[0] if candidates else ""))
    denominator = str(calculation.get("denominator_metric") or "")
    if operation in {"ratio_percent", "margin"}:
        ratio_metric, ratio_denominator = _ratio_metrics(intent, raw_question, candidates)
        metric = ratio_metric or metric
        denominator = denominator if denominator in NUMERIC_METRICS else ratio_denominator
        if metric == denominator:
            # `metric` (from the text-position fallback) and `denominator`
            # (kept as-is because Interpreter's seed value is itself a registered
            # metric) independently landed on the same metric — a self-ratio
            # is never valid. Retry with the position fallback's own
            # denominator before giving up.
            denominator = ratio_denominator if ratio_denominator != metric else None
    plan = _single_plan(intent, question=raw_question, operation=operation, metric=metric, denominator_metric=denominator or None)
    return validate_analysis_plan(plan) if plan is not None else None


def build_simple_analysis_plan(operation: str, metric: str = "", denominator_metric: str = "") -> dict[str, Any]:
    """Build the same canonical shape for the unused native ToolNode adapter."""

    metric = metric.strip()
    denominator_metric = denominator_metric.strip()
    requirements = [{"id": metric, "metric": metric, "role": "input", "companies": [], "periods": [], "required": True, "manifest_filter": {}}]
    inputs = [metric]
    if operation in {"ratio_percent", "margin"}:
        requirements.append({"id": denominator_metric, "metric": denominator_metric, "role": "denominator", "companies": [], "periods": [], "required": True, "manifest_filter": {}})
        inputs.append(denominator_metric)
    return validate_analysis_plan({
        "schema_version": PLAN_SCHEMA_VERSION,
        "status": "ready",
        "capability": "calculation",
        "requirements": requirements,
        "steps": [{"id": "calculate", "operation": operation, "inputs": inputs, "group_by": ["company", "period"]}],
        "output": {"ref": "calculate", "unit": "%" if operation in {"ratio_percent", "margin", "percentage_change", "cagr"} else "", "precision": 2},
    })


def build_state_analysis_plan(
    state: Mapping[str, Any],
    *,
    llm_client: PlanProposalProvider | None = None,
    llm_enabled: bool = False,
) -> dict[str, Any]:
    """Return planner-owned State fields, optionally using a guarded LLM proposal."""

    raw_intent = state.get("intent")
    intent = ReasonerIntent(
        question=str(state.get("question", "")),
        normalized_question=str(state.get("question", "")),
        route="ok",
        intent="unknown",
    )
    if isinstance(raw_intent, Mapping):
        from reasoner.adapters.interpreter import adapt_interpreter_intent

        intent = adapt_interpreter_intent(raw_intent, question=str(state.get("question", "")))
    question = str(state.get("question", intent.question))
    try:
        deterministic = build_analysis_plan(intent, question=question)
    except (TypeError, ValueError, KeyError) as error:
        deterministic = None
        deterministic_error = type(error).__name__
    else:
        deterministic_error = ""
    if deterministic is not None:
        return {
            "analysis_plan": deterministic,
            "plan_status": "ready",
            "plan_failure_reason": None,
            "plan_trace": ["planner=deterministic", f"steps={len(deterministic['steps'])}"],
        }

    if llm_enabled and llm_client is not None:
        try:
            proposal = llm_client.generate_json(
                [
                    {
                        "role": "system",
                        "content": (
                            "금융 공시 Fact로 실행할 수 있는 계산 계획만 JSON으로 제안하세요. "
                            "requirements[].metric과 output에 쓰는 모든 metric은 반드시 다음 등록된 "
                            f"값 중에서만 고르세요: {_numeric_metric_catalog()}. "
                            "steps[].operation은 반드시 다음 중에서만 고르세요: "
                            f"{', '.join(sorted(SUPPORTED_OPERATIONS))}. "
                            "질문에서 분자(numerator)로 요구한 수치와 분모(denominator)로 요구한 수치를 "
                            "위 metric 목록에서 각각 정확히 하나씩 고르세요. "
                            "값, 문서 ID, Python 코드, 목록에 없는 metric은 포함하지 마세요."
                        ),
                    },
                    {"role": "user", "content": str({"question": question, "intent": intent.to_dict()})},
                ],
                schema=ANALYSIS_PLAN_SCHEMA,
                operation="calculation_planning",
            )
            plan = validate_analysis_plan(proposal)
            return {
                "analysis_plan": plan,
                "plan_status": "ready",
                "plan_failure_reason": None,
                "plan_trace": ["planner=llm", f"steps={len(plan['steps'])}"],
            }
        except Exception:
            return {
                "analysis_plan": {},
                "plan_status": "unavailable",
                "plan_failure_reason": "llm_plan_invalid",
                "plan_trace": ["planner=llm", "planner_failed"],
            }

    reason = "deterministic_plan_unavailable"
    if deterministic_error:
        reason += f":{deterministic_error}"
    return {
        "analysis_plan": {},
        "plan_status": "unavailable",
        "plan_failure_reason": reason,
        "plan_trace": ["planner=unavailable"],
    }


__all__ = [
    "ANALYSIS_PLAN_SCHEMA",
    "MAX_PLAN_DEPTH",
    "MAX_PLAN_NODES",
    "NUMERIC_METRICS",
    "PlanProposalProvider",
    "SUPPORTED_OPERATIONS",
    "build_analysis_plan",
    "build_calculation_plan",
    "build_simple_analysis_plan",
    "build_state_analysis_plan",
    "validate_analysis_plan",
]
