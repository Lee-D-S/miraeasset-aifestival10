from __future__ import annotations

from typing import Any

from agentic_rag.deterministic.calculation_registry import execute_operation


def evaluate_expression(expression: dict[str, Any], variables: dict[str, float] | None = None, *, depth: int = 0) -> float:
    if depth > 16:
        raise ValueError("계산식 중첩 깊이가 제한을 초과했습니다.")
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
    if not isinstance(args, list) or len(args) != 2:
        raise ValueError("계산식은 두 개의 args를 가져야 합니다.")
    values = [evaluate_expression(item, variables, depth=depth + 1) for item in args]
    return execute_operation(operation, values)
