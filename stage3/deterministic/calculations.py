from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import replace
import re
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent
from stage3.deterministic.calculation_registry import execute_operation
from stage3.grounding import matching_facts, period_matches


_AMOUNT_METRICS = frozenset({"revenue", "operating_profit", "net_income"})
_AMOUNT_UNITS = frozenset({"백만원", "원", "천원", "억원", "조원", "조"})


def numeric_facts(facts: Iterable[Stage3Fact], *, metric: str | None = None) -> list[Stage3Fact]:
    selected: list[Stage3Fact] = []
    for fact in facts:
        if fact.kind != "numeric" or not isinstance(fact.normalized_value, (int, float)) or not fact.unit.strip():
            continue
        if metric is not None and fact.metric != metric:
            continue
        if metric in {"revenue", "operating_profit", "net_income"} and fact.unit == "%":
            continue
        selected.append(fact)
    return selected


def _requested_periods(intent: Stage3Intent) -> list[str]:
    time = intent.time or {}
    years = [str(year) for year in time.get("years") or []]
    months = [int(month) for month in time.get("base_months") or [] if str(month).isdigit()]
    if len(months) == len(years) and years:
        return [f"{year}-{month:02d}" for year, month in zip(years, months)]
    if len(months) == 1 and years:
        return [f"{year}-{months[0]:02d}" for year in years]
    return years


def _matches_period(fact: Stage3Fact, requested: str) -> bool:
    return period_matches(fact.period, requested)


def _pick_best(facts: Iterable[Stage3Fact]) -> Stage3Fact | None:
    candidates = list(facts)
    if not candidates:
        return None

    def rank(fact: Stage3Fact) -> tuple:
        unit = str(fact.unit or "")
        try:
            magnitude = abs(float(fact.normalized_value or fact.value))
        except (TypeError, ValueError):
            magnitude = 0.0
        amount_unit = 1 if unit in _AMOUNT_UNITS else 0
        not_percent = 0 if unit == "%" else 1
        label_match = 1 if fact.label in {"매출액", "매출"} else 0
        return (not_percent, label_match, amount_unit, fact.confidence, magnitude, str(fact.document_id))

    return max(candidates, key=rank)


def _filter_periods(facts: list[Stage3Fact], periods: list[str]) -> list[Stage3Fact]:
    if not periods:
        return facts
    return [fact for fact in facts if any(_matches_period(fact, period) for period in periods)]


def _currency_key(fact: Stage3Fact) -> str | None:
    if fact.unit == "%":
        return "%"
    return fact.currency


def _alignment_error(facts: list[Stage3Fact], *, same_period: bool) -> str | None:
    if not facts:
        return "insufficient_evidence"
    if any(not fact.period for fact in facts):
        return "invalid_period"
    if any(not fact.basis for fact in facts):
        return "invalid_basis"
    if len({fact.basis for fact in facts}) > 1:
        return "invalid_basis"
    if any(not fact.unit.strip() for fact in facts):
        return "invalid_unit"
    if any(_currency_key(fact) is None for fact in facts):
        return "invalid_currency"
    currencies = {_currency_key(fact) for fact in facts}
    if len(currencies) > 1:
        return "invalid_currency"
    if same_period and len({fact.period for fact in facts}) > 1:
        return "invalid_period"
    return None


def _error(operation: str, status: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": status, "error": message, "operation": operation, **extra}


def _common_display_unit(facts: list[Stage3Fact]) -> str:
    units = {fact.unit for fact in facts}
    if len(units) == 1:
        return next(iter(units))
    currencies = {_currency_key(fact) for fact in facts}
    if currencies == {"KRW"}:
        return "원"
    if currencies == {"%"}:
        return "%"
    return ""


def _display_amount(fact: Stage3Fact) -> float | str:
    if fact.display_value:
        return fact.display_value
    return fact.value


def _input_dict(fact: Stage3Fact) -> dict[str, Any]:
    return {
        "value": _display_amount(fact),
        "normalized_value": fact.normalized_value,
        "unit": fact.unit,
        "currency": fact.currency,
        "period": fact.period,
        "basis": fact.basis,
        "document_id": fact.document_id,
    }


def _preferred_metric_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent) -> list[Stage3Fact]:
    metric = str(intent.metric or "").strip().lower()
    items = list(facts)
    if metric == "revenue":
        labeled = [fact for fact in items if fact.label in {"매출액", "매출"}]
        if labeled:
            return labeled
    if metric == "operating_profit":
        labeled = [fact for fact in items if "영업이익" in fact.label and "률" not in fact.label]
        if labeled:
            return labeled
    if metric == "total_assets":
        question = f"{intent.question} {intent.normalized_question}".replace(" ", "")
        if "자기자본비율" in question:
            labeled = [fact for fact in items if "자기자본비율" in fact.label.replace(" ", "")]
            if labeled:
                return labeled
        if "부채비율" in question:
            labeled = [fact for fact in items if "부채비율" in fact.label.replace(" ", "")]
            if labeled:
                return labeled
    return items


def _calculation_result(operation: str, facts: list[Stage3Fact], result: float, formula: str, unit: str) -> dict[str, Any]:
    return {
        "status": "ok",
        "operation": operation,
        "inputs": [_input_dict(fact) for fact in facts],
        "formula": formula,
        "result": result,
        "unit": unit,
        "evidence_ids": list(dict.fromkeys(fact.document_id for fact in facts)),
    }


def infer_operation(intent: Stage3Intent) -> str:
    """Return only the operation explicitly selected by Stage1."""

    question_type = (intent.question_type or intent.intent or "").strip().lower()
    if question_type in {"compare", "comparison"}:
        return "rank"
    return str(intent.calculation.get("operation", "lookup"))


def _series_facts(selected: list[Stage3Fact], intent: Stage3Intent, periods: list[str]) -> list[Stage3Fact]:
    companies = intent.companies or intent.sector_members
    if companies:
        selected = [fact for fact in selected if fact.company in companies]
    if periods:
        selected = _filter_periods(selected, periods)
    grouped: dict[str, list[Stage3Fact]] = defaultdict(list)
    for fact in selected:
        grouped[str(fact.company or "미상")].append(fact)
    if companies:
        for company in companies:
            company_facts = grouped.get(company, [])
            if company_facts:
                return sorted(company_facts, key=lambda fact: (str(fact.period), str(fact.document_id)))
        return []
    if len(grouped) == 1:
        return sorted(next(iter(grouped.values())), key=lambda fact: (str(fact.period), str(fact.document_id)))
    if not grouped:
        return []
    best = max(grouped.values(), key=lambda group: len({fact.period for fact in group}))
    return sorted(best, key=lambda fact: (str(fact.period), str(fact.document_id)))


def _period_pair(selected: list[Stage3Fact], intent: Stage3Intent) -> tuple[Stage3Fact, Stage3Fact] | None:
    periods = _requested_periods(intent)
    if len(periods) >= 2:
        chosen = _requested_period_series(selected, intent, periods)
        if len(chosen) >= 2:
            return chosen[0], chosen[-1]
        return None
    series = _series_facts(selected, intent, [])
    unique: dict[str, Stage3Fact] = {}
    for fact in series:
        unique[str(fact.period)] = _pick_best([unique[str(fact.period)], fact]) if str(fact.period) in unique else fact
    values = sorted(unique.values(), key=lambda fact: str(fact.period))
    return (values[-2], values[-1]) if len(values) >= 2 else None


def _requested_period_series(
    selected: list[Stage3Fact], intent: Stage3Intent, periods: list[str]
) -> list[Stage3Fact]:
    """Return one best fact for every explicitly requested period."""

    series = _series_facts(selected, intent, periods)
    chosen: list[Stage3Fact] = []
    for period in periods:
        fact = _pick_best(fact for fact in series if _matches_period(fact, period))
        if fact is not None:
            chosen.append(fact)
    return chosen


def _compare_values(facts: list[Stage3Fact], intent: Stage3Intent, operation: str) -> dict[str, Any]:
    periods = _requested_periods(intent)
    expected = intent.companies or intent.sector_members
    grouped: dict[str, list[Stage3Fact]] = defaultdict(list)
    for fact in _filter_periods(facts, periods):
        grouped[str(fact.company or "미상")].append(fact)
    if expected:
        missing = [company for company in expected if not grouped.get(company)]
        if missing:
            return _error(operation, "insufficient_evidence", "비교 대상 기업의 수치 근거가 없습니다.", missing_companies=missing)
        chosen = [_pick_best(grouped[company]) for company in expected]
    else:
        chosen = [_pick_best(group) for group in grouped.values()]
    selected = [fact for fact in chosen if fact is not None]
    if len(selected) < 2:
        return _error(operation, "insufficient_evidence", "비교에 필요한 서로 다른 기업의 수치가 없습니다.")
    alignment_error = _alignment_error(selected, same_period=True)
    if alignment_error:
        messages = {
            "invalid_period": "비교 입력값의 기준 기간이 다릅니다.",
            "invalid_basis": "비교 입력값의 연결/별도 기준이 다릅니다.",
            "invalid_currency": "비교 입력값의 통화가 다릅니다.",
            "invalid_unit": "비교 입력값의 단위를 확인할 수 없습니다.",
        }
        return _error(operation, alignment_error, messages.get(alignment_error, "비교 입력값의 정합성이 맞지 않습니다."))
    display_unit = _common_display_unit(selected)
    ranked = sorted(selected, key=lambda fact: float(fact.normalized_value), reverse=True)
    results = [
        {
            "rank": index,
            "company": fact.company or "미상",
            "value": fact.normalized_value,
            "unit": display_unit,
            "period": fact.period,
            "basis": fact.basis,
            "document_id": fact.document_id,
        }
        for index, fact in enumerate(ranked, start=1)
    ]
    return {
        "status": "ok",
        "operation": operation,
        "results": results,
        "top": results[0],
        "evidence_ids": [item["document_id"] for item in results],
    }


def _ratio_inputs(
    selected: list[Stage3Fact],
    intent: Stage3Intent,
    numerator_metric: str,
    denominator_metric: str = "revenue",
) -> tuple[Stage3Fact, Stage3Fact] | None:
    periods = _requested_periods(intent)
    selected = _filter_periods(selected, periods)
    numerator = [fact for fact in selected if fact.metric == numerator_metric]
    denominator = [fact for fact in selected if fact.metric == denominator_metric]
    if denominator_metric == "revenue":
        denominator = [fact for fact in selected if fact.metric == "revenue" or fact.label in {"매출액", "매출"}]
    companies = intent.companies or intent.sector_members
    if companies:
        numerator = [fact for fact in numerator if fact.company in companies]
        denominator = [fact for fact in denominator if fact.company in companies]
    for left in numerator:
        right = _pick_best(fact for fact in denominator if fact.company == left.company and fact.period == left.period)
        if right:
            return left, right
    return None


def _coalesce_units(facts: list[Stage3Fact]) -> list[Stage3Fact]:
    units = [fact.unit for fact in facts if fact.unit]
    if not units:
        return facts
    preferred = "백만원" if "백만원" in units else max(set(units), key=units.count)
    same = [fact for fact in facts if fact.unit == preferred]
    return same or facts


def _plan_period_key(value: Any) -> tuple[int, str]:
    match = re.search(r"20\d{2}", str(value or ""))
    return (int(match.group(0)) if match else 0, str(value or ""))


def _record_key(record: Mapping[str, Any], group_by: list[str]) -> tuple[str, ...]:
    return tuple(str(record.get(field, "미상")) for field in group_by)


def _record_value(record: Mapping[str, Any]) -> float | None:
    value = record.get("value")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _derived_record(
    *,
    value: float,
    source_records: list[Mapping[str, Any]],
    operation: str,
    company: str | None = None,
    period: str | None = None,
    unit: str = "",
    formula: str = "",
) -> dict[str, Any]:
    evidence_ids = list(dict.fromkeys(
        str(evidence_id)
        for record in source_records
        for evidence_id in record.get("evidence_ids", [])
        if evidence_id
    ))
    return {
        "value": value,
        "company": company,
        "period": period,
        "unit": unit,
        "basis": next((str(record.get("basis")) for record in source_records if record.get("basis")), None),
        "evidence_ids": evidence_ids,
        "inputs": [dict(record.get("input", {})) for record in source_records],
        "formula": formula or operation,
        "operation": operation,
    }


def _requirement_intent(intent: Stage3Intent | None, requirement: Mapping[str, Any]) -> Stage3Intent | None:
    if intent is None:
        return None
    years: list[int] = []
    for period in requirement.get("periods", []) or []:
        year = str(period)[:4]
        if year.isdigit():
            years.append(int(year))
    companies = [str(item) for item in requirement.get("companies", []) if str(item).strip()]
    time = dict(intent.time or {})
    if years:
        time["years"] = years
    manifest = dict(intent.manifest_filter or {})
    if companies:
        manifest["corp_names"] = list(dict.fromkeys([*manifest.get("corp_names", []), *companies]))
    return replace(
        intent,
        metric=str(requirement.get("metric") or intent.metric or ""),
        companies=companies or list(intent.companies),
        time=time,
        manifest_filter=manifest,
    )


def _record_from_fact(fact: Stage3Fact) -> dict[str, Any]:
    return {
        "value": float(fact.normalized_value),
        "company": fact.company,
        "period": fact.period,
        "unit": fact.unit,
        "label": fact.label,
        "basis": fact.basis,
        "currency": fact.currency,
        "evidence_ids": [fact.document_id],
        "input": {
            "value": fact.display_value or fact.value,
            "normalized_value": fact.normalized_value,
            "unit": fact.unit,
            "period": fact.period,
            "basis": fact.basis,
            "document_id": fact.document_id,
        },
    }


def _requirement_records(
    requirement: Mapping[str, Any],
    facts: list[Stage3Fact],
    *,
    intent: Stage3Intent | None = None,
) -> list[dict[str, Any]]:
    metric = str(requirement.get("metric", ""))
    companies = {str(item) for item in requirement.get("companies", []) if str(item).strip()}
    periods = {str(item) for item in requirement.get("periods", []) if str(item).strip()}
    selected: list[Stage3Fact] = []
    for fact in facts:
        if fact.kind != "numeric" or not isinstance(fact.normalized_value, (int, float)) or not fact.unit.strip():
            continue
        if fact.metric != metric:
            continue
        if metric in _AMOUNT_METRICS and fact.unit == "%":
            continue
        if companies and str(fact.company) not in companies:
            continue
        if periods and not any(period_matches(fact.period, period) for period in periods):
            continue
        selected.append(fact)
    grounded_intent = _requirement_intent(intent, requirement)
    if grounded_intent is not None:
        grounded = matching_facts(selected, grounded_intent, require_scope=True)
        if grounded:
            selected = grounded
        else:
            selected = []
    return [_record_from_fact(fact) for fact in selected]


def _pick_best_record(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None

    def rank(record: Mapping[str, Any]) -> tuple:
        unit = str(record.get("unit") or "")
        value = abs(_record_value(record) or 0.0)
        amount_unit = 1 if unit in _AMOUNT_UNITS else 0
        not_percent = 0 if unit == "%" else 1
        label = str(record.get("label") or "")
        label_match = 1 if label in {"매출액", "매출"} else 0
        return (not_percent, label_match, amount_unit, value)

    return max(records, key=rank)


def _best_records_by_year(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        year, _ = _plan_period_key(record.get("period"))
        if year <= 0:
            continue
        grouped[year].append(record)
    chosen: list[dict[str, Any]] = []
    for year in sorted(grouped):
        best = _pick_best_record(grouped[year])
        if best is not None:
            chosen.append(best)
    return chosen


def _pair_records(left: list[dict[str, Any]], right: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in right:
        index[(str(record.get("company")), str(record.get("period")))].append(record)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in left:
        candidates = index.get((str(record.get("company")), str(record.get("period"))), [])
        if candidates:
            compatible = next(
                (
                    candidate
                    for candidate in candidates
                    if (not record.get("basis") or not candidate.get("basis") or record.get("basis") == candidate.get("basis"))
                    and (not record.get("currency") or not candidate.get("currency") or record.get("currency") == candidate.get("currency"))
                ),
                None,
            )
            if compatible is not None:
                pairs.append((record, compatible))
    return pairs


def _execute_plan_step(operation: str, inputs: list[list[dict[str, Any]]], step: Mapping[str, Any]) -> list[dict[str, Any]]:
    if not inputs or any(not values for values in inputs):
        return []
    group_by = [str(value) for value in step.get("group_by", []) if value in {"company", "period"}]
    if operation in {"ratio_percent", "margin"}:
        results: list[dict[str, Any]] = []
        for numerator, denominator in _pair_records(inputs[0], inputs[1]):
            left = _record_value(numerator)
            right = _record_value(denominator)
            if left is None or right in (None, 0):
                continue
            result = execute_operation("margin" if operation == "margin" else "ratio_percent", [left, right])
            results.append(_derived_record(
                value=result,
                source_records=[numerator, denominator],
                operation=operation,
                company=str(numerator.get("company")) if numerator.get("company") is not None else None,
                period=str(numerator.get("period")) if numerator.get("period") is not None else None,
                unit="%",
                formula="numerator/denominator*100",
            ))
        return results
    if operation == "percentage_point_change":
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in inputs[0]:
            grouped[str(record.get("company", "미상"))].append(record)
        results = []
        for company, records in grouped.items():
            ordered = sorted(records, key=lambda record: _plan_period_key(record.get("period")))
            if len(ordered) < 2:
                continue
            old, new = ordered[-2], ordered[-1]
            old_value = _record_value(old)
            new_value = _record_value(new)
            if old_value is None or new_value is None:
                continue
            results.append(_derived_record(
                value=execute_operation("subtract", [new_value, old_value]),
                source_records=[old, new],
                operation=operation,
                company=company,
                period=str(new.get("period")) if new.get("period") is not None else None,
                unit="percentage_points",
                formula="current_ratio-previous_ratio",
            ))
        return results
    if operation in {"percentage_change", "cagr"}:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for record in inputs[0]:
            grouped[str(record.get("company", "미상"))].append(record)
        results = []
        for company, records in grouped.items():
            ordered = _best_records_by_year(records)
            if len(ordered) < 2:
                continue
            old, new = ordered[0], ordered[-1]
            old_value = _record_value(old)
            new_value = _record_value(new)
            if old_value is None or new_value is None:
                continue
            if operation == "cagr":
                old_year = _plan_period_key(old.get("period"))[0]
                new_year = _plan_period_key(new.get("period"))[0]
                if old_year <= 0 or new_year <= old_year:
                    continue
                value = execute_operation("cagr", [old_value, new_value], periods=new_year - old_year)
            else:
                value = execute_operation("percentage_change", [old_value, new_value])
            results.append(_derived_record(
                value=value,
                source_records=[old, new],
                operation=operation,
                company=company,
                period=str(new.get("period")) if new.get("period") is not None else None,
                unit="%",
                formula="(new-old)/abs(old)*100" if operation == "percentage_change" else "(new/old)^(1/years)-1*100",
            ))
            if len(ordered) > 2:
                series = []
                evidence_ids: list[str] = []
                for item in ordered:
                    payload = item.get("input") if isinstance(item.get("input"), dict) else {}
                    evidence_id = next((str(value) for value in item.get("evidence_ids") or [] if value), "")
                    if evidence_id:
                        evidence_ids.append(evidence_id)
                    series.append({
                        "period": item.get("period"),
                        "value": payload.get("value", item.get("value")),
                        "unit": payload.get("unit") or item.get("unit"),
                        "document_id": evidence_id,
                    })
                results[-1]["series"] = series
                results[-1]["evidence_ids"] = list(dict.fromkeys(evidence_ids))
        return results
    if operation == "rank":
        records = [record for record in inputs[0] if _record_value(record) is not None]
        if len({str(record.get("period")) for record in records}) > 1:
            return []
        ranked_values = sorted((_record_value(record) for record in records), reverse=True)
        results = []
        for record in records:
            value = _record_value(record)
            if value is None:
                continue
            derived = _derived_record(
                value=value,
                source_records=[record],
                operation=operation,
                company=str(record.get("company")) if record.get("company") is not None else None,
                period=str(record.get("period")) if record.get("period") is not None else None,
                unit=str(record.get("unit", "")),
                formula="rank(value across companies)",
            )
            derived["rank"] = 1 + sum(item > value for item in ranked_values)
            results.append(derived)
        return sorted(results, key=lambda record: (int(record["rank"]), str(record.get("company", ""))))
    if len(inputs) == 1 and operation in {"sum", "average", "min", "max"}:
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
        for record in inputs[0]:
            groups[_record_key(record, group_by)].append(record)
        if not group_by:
            groups[()] = list(inputs[0])
        results = []
        for records in groups.values():
            values = [value for record in records if (value := _record_value(record)) is not None]
            if not values:
                continue
            results.append(_derived_record(
                value=execute_operation(operation, values),
                source_records=records,
                operation=operation,
                company=str(records[0].get("company")) if group_by == ["company"] else None,
                period=str(records[0].get("period")) if group_by == ["period"] else None,
                unit=str(records[0].get("unit", "")),
            ))
        return results
    if operation in {"add", "subtract", "multiply", "divide"} and len(inputs) == 2:
        results = []
        for left, right in _pair_records(inputs[0], inputs[1]):
            left_value = _record_value(left)
            right_value = _record_value(right)
            if left_value is None or right_value is None:
                continue
            try:
                value = execute_operation(operation, [left_value, right_value])
            except (TypeError, ValueError, ZeroDivisionError):
                continue
            results.append(_derived_record(
                value=value,
                source_records=[left, right],
                operation=operation,
                company=str(left.get("company")) if left.get("company") is not None else None,
                period=str(left.get("period")) if left.get("period") is not None else None,
                unit=str(left.get("unit", "")) if operation in {"add", "subtract"} else "",
            ))
        return results
    return []


def execute_analysis_plan(
    plan: Mapping[str, Any],
    facts: Iterable[Stage3Fact],
    *,
    intent: Stage3Intent | None = None,
) -> dict[str, Any]:
    """Execute a validated plan over grounded Facts and preserve provenance."""

    fact_list = list(facts)
    requirements = {
        str(item.get("id")): item
        for item in plan.get("requirements", [])
        if isinstance(item, Mapping) and item.get("id")
    }
    records: dict[str, list[dict[str, Any]]] = {
        identifier: _requirement_records(requirement, fact_list, intent=intent)
        for identifier, requirement in requirements.items()
    }
    calculations: list[dict[str, Any]] = []
    comparisons: list[dict[str, Any]] = []
    derived_facts: list[dict[str, Any]] = []
    warnings: list[str] = []
    failed = [identifier for identifier, values in records.items() if requirements[identifier].get("required", True) and not values]
    if failed:
        warnings.append("필수 계산 입력 근거가 없습니다: " + ", ".join(failed))
    for step in plan.get("steps", []):
        if not isinstance(step, Mapping):
            continue
        identifier = str(step.get("id", ""))
        operation = str(step.get("operation", ""))
        inputs = [records.get(str(value), []) for value in step.get("inputs", [])]
        values = _execute_plan_step(operation, inputs, step)
        records[identifier] = values
        for record in values:
            source_id = next(iter(record.get("evidence_ids", [])), "")
            derived_facts.append({
                "metric": f"derived:{identifier}",
                "label": identifier,
                "value": record["value"],
                "raw_value": record["value"],
                "unit": record.get("unit", ""),
                "normalized_value": record["value"],
                "period": record.get("period"),
                "basis": record.get("basis"),
                "company": record.get("company"),
                "document_id": source_id,
                "source": "calculation",
                "evidence": record.get("formula", operation),
                "confidence": 1.0,
                "kind": "derived",
                "currency": None,
                "table_context": {"step_id": identifier, "input_fact_ids": record.get("evidence_ids", [])},
            })
        if not values:
            warnings.append(f"계산 단계의 입력이 부족합니다: {identifier}")
        elif operation == "rank" and identifier == str(plan.get("output", {}).get("ref")):
            results = [
                {
                    "rank": record["rank"],
                    "company": record.get("company") or "미상",
                    "value": record["value"],
                    "unit": record.get("unit", ""),
                    "period": record.get("period"),
                    "document_id": next(iter(record.get("evidence_ids", [])), ""),
                    "evidence_ids": list(record.get("evidence_ids", [])),
                }
                for record in values
            ]
            comparisons.append({
                "status": "ok",
                "operation": "rank",
                "results": results,
                "top": results[0] if results else {},
                "evidence_ids": list(dict.fromkeys(
                    evidence_id
                    for item in results
                    for evidence_id in item.get("evidence_ids", [])
                    if evidence_id
                )),
                "plan_step_id": identifier,
            })
        elif values:
            if len(values) == 1:
                record = values[0]
                calc = {
                    "status": "ok",
                    "operation": operation,
                    "inputs": record.get("inputs", []),
                    "formula": record.get("formula", operation),
                    "result": record["value"],
                    "unit": record.get("unit", ""),
                    "evidence_ids": record.get("evidence_ids", []),
                    "plan_step_id": identifier,
                }
                if record.get("series"):
                    calc["series"] = list(record["series"])
                calculations.append(calc)
            else:
                calculations.append({
                    "status": "derived",
                    "operation": operation,
                    "results": [dict(record) for record in values],
                    "evidence_ids": list(dict.fromkeys(
                        evidence_id
                        for record in values
                        for evidence_id in record.get("evidence_ids", [])
                    )),
                    "plan_step_id": identifier,
                })
    output_ref = str(plan.get("output", {}).get("ref", ""))
    success = bool(records.get(output_ref)) and not failed
    return {
        "success": success,
        "calculations": calculations,
        "comparisons": comparisons,
        "derived_facts": derived_facts,
        "warnings": warnings,
        "output_records": records.get(output_ref, []),
    }


def calculate_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent, *, operation: str | None = None) -> dict[str, Any]:
    """Run a whitelist calculation only on aligned, grounded Facts."""

    all_numeric = numeric_facts(facts)
    operation = operation or infer_operation(intent)
    if operation in {"ratio_percent", "margin"}:
        selected = all_numeric
    else:
        selected = _preferred_metric_facts(numeric_facts(all_numeric, metric=intent.metric), intent)
    if not selected:
        selected = numeric_facts(all_numeric, metric=intent.metric)
    if not selected:
        return _error(operation, "insufficient_evidence", "계산에 필요한 수치 근거가 없습니다.")
    if operation in {"percentage_change", "cagr"}:
        grounded = matching_facts(selected, intent, require_scope=True)
        if grounded:
            selected = grounded
    elif operation in {"sum", "average", "min", "max"}:
        selected = _coalesce_units(selected)

    if operation in {"compare", "rank"}:
        return _compare_values(selected, intent, operation)
    if operation in {"percentage_change", "cagr"}:
        periods = _requested_periods(intent)
        requested_series = (
            _requested_period_series(selected, intent, periods)
            if len(periods) >= 2
            else []
        )
        pair = _period_pair(selected, intent)
        if pair is None:
            return _error(operation, "insufficient_evidence", "계산에 필요한 요청 기간의 수치가 없습니다.")
        old, new = pair
        alignment_error = _alignment_error([old, new], same_period=False)
        if alignment_error:
            messages = {
                "invalid_basis": "계산 입력값의 연결/별도 기준이 다릅니다.",
                "invalid_currency": "계산 입력값의 통화가 다릅니다.",
                "invalid_unit": "계산 입력값의 단위를 확인할 수 없습니다.",
                "invalid_period": "계산 입력값의 기준 기간을 확인할 수 없습니다.",
            }
            return _error(operation, alignment_error, messages.get(alignment_error, "계산 입력값의 정합성이 맞지 않습니다."))
        if operation == "cagr":
            years = [
                int(period[:4])
                for period in (periods[0], periods[-1])
                if period[:4].isdigit()
            ] if len(periods) >= 2 else []
            years_elapsed = abs(years[1] - years[0]) if len(years) == 2 else None
            if not years_elapsed:
                return _error(operation, "invalid_period", "CAGR 기간을 확인할 수 없습니다.")
            result = execute_operation(operation, [float(old.normalized_value), float(new.normalized_value)], periods=years_elapsed)
            formula = "(new/old)^(1/years)-1*100"
        else:
            result = execute_operation(operation, [float(old.normalized_value), float(new.normalized_value)])
            formula = "(new-old)/abs(old)*100"
        calculation = _calculation_result(operation, [old, new], result, formula, "%")
        if len(requested_series) > 2:
            calculation["series"] = [
                {
                    "period": fact.period,
                    "value": _display_amount(fact),
                    "unit": fact.unit,
                    "document_id": fact.document_id,
                }
                for fact in requested_series
            ]
            calculation["evidence_ids"] = list(
                dict.fromkeys(fact.document_id for fact in requested_series)
            )
        return calculation
    if operation in {"ratio_percent", "margin"}:
        numerator_metric = "operating_profit" if operation == "margin" else (intent.metric or "")
        denominator_metric = str(intent.calculation.get("denominator_metric") or "revenue")
        pair = _ratio_inputs(selected, intent, numerator_metric, denominator_metric)
        if pair is None:
            return _error(operation, "insufficient_evidence", "비중·마진 계산에 필요한 분자·분모가 없습니다.")
        numerator, denominator = pair
        alignment_error = _alignment_error([numerator, denominator], same_period=True)
        if alignment_error:
            return _error(operation, alignment_error, "비중·마진 입력값의 기간·기준·통화가 다릅니다.")
        result = execute_operation("margin" if operation == "margin" else "ratio", [float(numerator.normalized_value), float(denominator.normalized_value)])
        if operation == "ratio_percent":
            result *= 100
        return _calculation_result(operation, [numerator, denominator], result, "numerator/denominator*100", "%")
    if operation in {"add", "subtract", "multiply", "divide"}:
        selected = _filter_periods(selected, _requested_periods(intent))
        if len(selected) < 2:
            return _error(operation, "insufficient_evidence", "사칙연산에 필요한 두 개 이상의 수치 근거가 없습니다.")
        operands = sorted(selected, key=lambda fact: (str(fact.period), str(fact.document_id)))[:2]
        alignment_error = _alignment_error(operands, same_period=False)
        if alignment_error:
            return _error(operation, alignment_error, "사칙연산 입력값의 기간·기준·통화·단위가 다릅니다.")
        result = execute_operation(operation, [float(fact.normalized_value) for fact in operands])
        formula = {
            "add": "left+right",
            "subtract": "left-right",
            "multiply": "left*right",
            "divide": "left/right",
        }[operation]
        unit = _common_display_unit(operands) if operation in {"add", "subtract"} else ""
        return _calculation_result(operation, operands, result, formula, unit)
    if operation in {"sum", "average", "min", "max"}:
        selected = _filter_periods(selected, _requested_periods(intent))
        if not selected:
            return _error(operation, "insufficient_evidence", "요청 기간의 수치 근거가 없습니다.")
        alignment_error = _alignment_error(selected, same_period=True)
        if alignment_error:
            return _error(operation, alignment_error, "목록 계산 입력값의 기간·기준·통화가 다릅니다.")
        result = execute_operation(operation, [float(fact.normalized_value) for fact in selected])
        return _calculation_result(operation, selected, result, operation, _common_display_unit(selected))
    return _error(operation, "unsupported", f"지원하지 않는 계산 연산입니다: {operation}")
