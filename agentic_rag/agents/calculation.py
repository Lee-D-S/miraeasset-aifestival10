from __future__ import annotations

import re
from typing import Any

from agentic_rag.agents.calculation_planner import make_calculation_planner
from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.calculation_registry import execute_operation
from agentic_rag.deterministic.calculation_schema import validate_calculation_plan


METRIC_LABELS = {
    "revenue": ("매출액", "매출"),
    "operating_income": ("영업이익",),
    "net_income": ("당기순이익", "순이익"),
    "assets": ("총자산", "자산"),
    "liabilities": ("부채총계", "부채"),
    "equity": ("자본총계", "자본"),
    "current_assets": ("유동자산",),
    "current_liabilities": ("유동부채",),
}

UNIT_MULTIPLIERS = {"조원": 1_000_000_000_000, "억원": 100_000_000, "만원": 10_000, "천원": 1_000, "원": 1}


def extract_metric_values(documents: list[dict[str, Any]], metric: str) -> list[dict[str, Any]]:
    labels = METRIC_LABELS.get(metric, ())
    if not labels:
        return []
    pattern = re.compile(r"(?:" + "|".join(map(re.escape, labels)) + r")[^\d-]*(?P<value>-?\d[\d,]*(?:\.\d+)?)[ ]*(?P<unit>조원|억원|만원|천원|원)?")
    values: list[dict[str, Any]] = []
    for document in documents:
        for match in pattern.finditer(str(document.get("text", ""))):
            raw_value = float(match.group("value").replace(",", ""))
            unit = match.group("unit") or ""
            values.append({"value": raw_value * UNIT_MULTIPLIERS.get(unit, 1), "raw_value": raw_value, "unit": unit, "document_id": str(document.get("id", "")), "source": str(document.get("source", "")), "metadata": document.get("metadata", {})})
    return values


def _documents_for_plan(state: dict[str, Any], plan: dict[str, Any]) -> list[dict[str, Any]]:
    documents = state.get("cited_documents", []) or state.get("retrieved_documents", [])
    targets = {str(item) for item in plan.get("targets", []) if item}
    periods = {str(item) for item in plan.get("periods", []) if item}
    selected = []
    for document in documents:
        metadata = document.get("metadata", {}) or {}
        if targets and str(metadata.get("corp_name", "")) not in targets:
            continue
        if periods and str(metadata.get("report_period", "")) not in periods:
            continue
        selected.append(document)
    return selected or documents


def _execute_plan(plan: dict[str, Any], documents: list[dict[str, Any]]) -> tuple[dict[str, Any], tuple[str, ...]]:
    operation = str(plan["operation"])
    metric = str(plan.get("metric", "revenue"))
    evidence: list[str] = []
    if operation in {"margin", "debt_ratio", "current_ratio", "ratio"}:
        numerator_metric = {"margin": metric, "debt_ratio": "liabilities", "current_ratio": "current_assets"}.get(operation, metric)
        denominator_metric = {"margin": "revenue", "debt_ratio": "equity", "current_ratio": "current_liabilities"}.get(operation, "revenue")
        numerator = extract_metric_values(documents, numerator_metric)
        denominator = extract_metric_values(documents, denominator_metric)
        if not numerator or not denominator:
            raise ValueError(f"{operation} 계산에 필요한 지표가 부족합니다.")
        arguments = [numerator[0]["value"], denominator[0]["value"]]
        evidence.extend([numerator[0]["document_id"], denominator[0]["document_id"]])
        return {"operation": operation, "metric": metric, "inputs": arguments, "formula": f"{numerator_metric}/{denominator_metric}", "result": execute_operation(operation, arguments)}, tuple(dict.fromkeys(evidence))
    values = extract_metric_values(documents, metric)
    if len(values) < 2:
        raise ValueError(f"{metric} 계산에 필요한 두 개 이상의 수치를 찾지 못했습니다.")
    evidence.extend(item["document_id"] for item in values[:2])
    units = {item["unit"] for item in values[:2]}
    if "" in units and len(units) > 1:
        raise ValueError("계산 입력값의 단위가 일부 누락되어 확인할 수 없습니다.")
    arguments = [item["value"] for item in values[:2]]
    if operation == "compare":
        return {"operation": operation, "metric": metric, "inputs": arguments, "result": arguments[1] - arguments[0], "formula": "right-left"}, tuple(dict.fromkeys(evidence))
    periods = plan.get("periods", [])
    period_count = len(periods) - 1 if len(periods) >= 2 else None
    result = execute_operation(operation, arguments, periods=period_count)
    formula = {"percentage_change": "(new-old)/abs(old)*100", "cagr": "((new/old)^(1/periods)-1)*100"}.get(operation, operation)
    return {"operation": operation, "metric": metric, "inputs": arguments, "formula": formula, "result": result, operation: result}, tuple(dict.fromkeys(evidence))


def make_calculation_agent(client: Any | None = None):
    planner = make_calculation_planner(client)

    def agent(state: dict[str, Any]) -> dict[str, Any]:
        question = state.get("normalized_question", state.get("question", ""))
        plan, planner_source = planner(question)
        if plan is None:
            calculations = {"error": "계산 방법을 안전하게 구조화하지 못했습니다."}
            evidence_ids: tuple[str, ...] = ()
            confidence = 0.0
            status = "insufficient"
        else:
            metadata = state.get("metadata", {}) or {}
            plan = {**plan}
            if not plan.get("targets") and metadata.get("comparison_targets"):
                plan["targets"] = list(metadata["comparison_targets"])
            if not plan.get("periods") and metadata.get("report_period"):
                plan["periods"] = [metadata["report_period"]]
            valid, reason = validate_calculation_plan(plan)
            try:
                if not valid:
                    raise ValueError(reason)
                calculations, evidence_ids = _execute_plan(plan, _documents_for_plan(state, plan))
                confidence = 0.9
                status = "ok"
            except (TypeError, ValueError, ZeroDivisionError) as error:
                calculations = {"error": str(error), "plan": plan}
                evidence_ids = ()
                confidence = 0.2
                status = "insufficient"
        calculations["plan"] = plan
        sources = tuple(str(item.get("source", "")) for item in state.get("cited_documents", []) if str(item.get("id", "")) in evidence_ids)
        result = AgentResult("calculation", status, calculations=calculations, evidence_ids=evidence_ids, confidence=confidence, trace=(f"planner={planner_source}", f"operation={plan.get('operation') if plan else 'none'}"))
        provenance = Provenance("calculation", question, evidence_ids, sources, confidence, {"plan": plan, "planner": planner_source, "calculations": calculations})
        return {"calculations": calculations, "calculation_plan": plan or {}, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}
    return agent


calculation_agent = make_calculation_agent()
