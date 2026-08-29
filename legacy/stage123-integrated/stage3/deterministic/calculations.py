from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent
from stage3.deterministic.calculation_registry import execute_operation


def numeric_facts(facts: Iterable[Stage3Fact], *, metric: str | None = None) -> list[Stage3Fact]:
    return [
        fact
        for fact in facts
        if fact.kind == "numeric"
        and isinstance(fact.normalized_value, (int, float))
        and fact.unit.strip()
        and (metric is None or fact.metric == metric)
    ]


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
    period = str(fact.period or "")
    return period == requested or (len(requested) == 4 and period.startswith(requested))


def _pick_best(facts: Iterable[Stage3Fact]) -> Stage3Fact | None:
    candidates = list(facts)
    if not candidates:
        return None
    return max(candidates, key=lambda fact: (fact.confidence, str(fact.document_id)))


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


def _input_dict(fact: Stage3Fact) -> dict[str, Any]:
    return {
        "value": fact.normalized_value,
        "unit": fact.unit,
        "currency": fact.currency,
        "period": fact.period,
        "basis": fact.basis,
        "document_id": fact.document_id,
    }


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
    question = intent.normalized_question
    if intent.intent in {"compare", "comparison"} or any(word in question for word in ("비교", "어느 기업", "가장 큰", "순위")):
        return "rank"
    if any(word in question for word in ("증감률", "증가율", "감소율", "성장률")):
        return "percentage_change"
    if "CAGR" in question.upper() or "연평균" in question:
        return "cagr"
    if "영업이익률" in question or "마진" in question:
        return "margin"
    if "비중" in question or "비율" in question:
        return "ratio_percent"
    if "합계" in question or "총액" in question:
        return "sum"
    return "compare" if intent.intent in {"compare", "comparison"} else "lookup"


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
        series = _series_facts(selected, intent, periods)
        chosen = [_pick_best(fact for fact in series if _matches_period(fact, period)) for period in periods[:2]]
        if all(chosen):
            return chosen[0], chosen[1]  # type: ignore[return-value]
        return None
    series = _series_facts(selected, intent, [])
    unique: dict[str, Stage3Fact] = {}
    for fact in series:
        unique[str(fact.period)] = _pick_best([unique[str(fact.period)], fact]) if str(fact.period) in unique else fact
    values = sorted(unique.values(), key=lambda fact: str(fact.period))
    return (values[-2], values[-1]) if len(values) >= 2 else None


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


def _ratio_inputs(selected: list[Stage3Fact], intent: Stage3Intent, numerator_metric: str) -> tuple[Stage3Fact, Stage3Fact] | None:
    periods = _requested_periods(intent)
    selected = _filter_periods(selected, periods)
    numerator = [fact for fact in selected if fact.metric == numerator_metric]
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


def calculate_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent, *, operation: str | None = None) -> dict[str, Any]:
    """Run a whitelist calculation only on aligned, grounded Facts."""

    all_numeric = numeric_facts(facts)
    operation = operation or infer_operation(intent)
    if operation in {"ratio_percent", "margin"}:
        selected = all_numeric
    else:
        selected = numeric_facts(all_numeric, metric=intent.metric)
    if not selected:
        return _error(operation, "insufficient_evidence", "계산에 필요한 수치 근거가 없습니다.")

    if operation in {"compare", "rank"}:
        return _compare_values(selected, intent, operation)
    if operation in {"percentage_change", "cagr"}:
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
        periods = _requested_periods(intent)
        if operation == "cagr":
            years = [int(period[:4]) for period in periods[:2] if period[:4].isdigit()]
            years_elapsed = abs(years[1] - years[0]) if len(years) == 2 else None
            if not years_elapsed:
                return _error(operation, "invalid_period", "CAGR 기간을 확인할 수 없습니다.")
            result = execute_operation(operation, [float(old.normalized_value), float(new.normalized_value)], periods=years_elapsed)
            formula = "(new/old)^(1/years)-1*100"
        else:
            result = execute_operation(operation, [float(old.normalized_value), float(new.normalized_value)])
            formula = "(new-old)/abs(old)*100"
        return _calculation_result(operation, [old, new], result, formula, "%")
    if operation in {"ratio_percent", "margin"}:
        numerator_metric = "operating_profit" if operation == "margin" else (intent.metric or "")
        pair = _ratio_inputs(selected, intent, numerator_metric)
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
