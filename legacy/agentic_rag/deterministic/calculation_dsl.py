from __future__ import annotations

from typing import Any

from agentic_rag.deterministic.calculation_registry import execute_operation


VARIABLES = {"revenue", "operating_income", "net_income", "assets", "liabilities", "equity", "current_assets", "current_liabilities", "inventory"}
VARIABLE_ARITY = {"sum": (1, None), "average": (1, None), "median": (1, None), "min": (1, None), "max": (1, None), "rolling_average": (1, None), "rank": (2, None), "if": (3, 3)}


def validate_expression(expression: Any, variables: set[str] | None = None, *, depth: int = 0) -> tuple[bool, str]:
    variables = variables or VARIABLES
    if depth > 16:
        return False, "계산식 중첩 깊이가 제한을 초과했습니다."
    if not isinstance(expression, dict):
        return False, "계산식 노드는 객체여야 합니다."
    if "value" in expression:
        if not isinstance(expression["value"], (int, float)) or isinstance(expression["value"], bool):
            return False, "계산식 value는 숫자여야 합니다."
        return True, "확인되었습니다."
    if "variable" in expression:
        if str(expression["variable"]) not in variables:
            return False, f"허용되지 않은 계산식 변수입니다: {expression['variable']}"
        return True, "확인되었습니다."
    operation = str(expression.get("op", ""))
    if operation not in {"add", "subtract", "multiply", "divide", "power", "percentage_change", "period_change", "cagr", "margin", "ratio", "debt_ratio", "current_ratio", "sum", "average", "median", "min", "max", "rolling_average", "greater_than", "less_than", "difference", "threshold", "rank", "if"}:
        return False, f"허용되지 않은 계산식 연산입니다: {operation}"
    args = expression.get("args")
    if not isinstance(args, list):
        return False, "계산식 args는 배열이어야 합니다."
    minimum, maximum = VARIABLE_ARITY.get(operation, (2, 2))
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        return False, f"{operation}의 인자 수가 올바르지 않습니다."
    for child in args:
        valid, reason = validate_expression(child, variables, depth=depth + 1)
        if not valid:
            return False, reason
    return True, "확인되었습니다."


def evaluate_expression(expression: dict[str, Any], variables: dict[str, float] | None = None, *, depth: int = 0) -> float:
    valid, reason = validate_expression(expression, set((variables or {}).keys()) | VARIABLES)
    if not valid:
        raise ValueError(reason)
    variables = variables or {}
    if "value" in expression:
        value = expression["value"]
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError("계산식 value는 숫자여야 합니다.")
        return float(value)
    if "variable" in expression:
        name = str(expression["variable"])
        if name not in variables:
            raise ValueError(f"계산식 변수가 없습니다: {name}")
        return float(variables[name])
    operation = str(expression.get("op", ""))
    args = expression.get("args")
    operation = str(expression.get("op", ""))
    if operation == "if":
        condition = evaluate_expression(args[0], variables, depth=depth + 1)
        return evaluate_expression(args[1] if condition else args[2], variables, depth=depth + 1)
    values = [evaluate_expression(item, variables, depth=depth + 1) for item in args]
    return execute_operation(operation, values)
