from __future__ import annotations

from typing import Any


ALLOWED_OPERATIONS = {"add", "subtract", "multiply", "divide", "percentage_change", "cagr", "margin", "ratio", "debt_ratio", "current_ratio", "compare"}
ALLOWED_METRICS = {"revenue", "operating_income", "net_income", "assets", "liabilities", "equity", "current_assets", "current_liabilities"}


def validate_calculation_plan(plan: Any) -> tuple[bool, str]:
    if not isinstance(plan, dict):
        return False, "계산 계획은 JSON 객체여야 합니다."
    operation = str(plan.get("operation", ""))
    if operation not in ALLOWED_OPERATIONS:
        return False, f"허용되지 않은 계산 연산입니다: {operation}"
    metric = str(plan.get("metric", ""))
    if metric and metric not in ALLOWED_METRICS:
        return False, f"허용되지 않은 계산 지표입니다: {metric}"
    periods = plan.get("periods", [])
    if periods and (not isinstance(periods, list) or not all(isinstance(item, (str, int)) for item in periods)):
        return False, "계산 기간 형식이 올바르지 않습니다."
    targets = plan.get("targets", [])
    if targets and (not isinstance(targets, list) or not all(isinstance(item, str) for item in targets)):
        return False, "계산 대상 형식이 올바르지 않습니다."
    sub_operations = plan.get("sub_operations", [])
    if sub_operations and (not isinstance(sub_operations, list) or not all(str(item) in ALLOWED_OPERATIONS or str(item).endswith("_lookup") for item in sub_operations)):
        return False, "허용되지 않은 하위 계산 연산입니다."
    return True, "계산 계획이 확인되었습니다."
