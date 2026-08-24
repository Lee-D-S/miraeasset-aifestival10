from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agentic_rag.deterministic.calculations import percentage_change


def add(left: float, right: float) -> float:
    return left + right


def subtract(left: float, right: float) -> float:
    return left - right


def multiply(left: float, right: float) -> float:
    return left * right


def divide(left: float, right: float) -> float:
    if right == 0:
        raise ValueError("Cannot divide by zero")
    return left / right


def cagr(old: float, new: float, periods: float) -> float:
    if old <= 0 or new < 0 or periods <= 0:
        raise ValueError("CAGR requires positive old value and period")
    return ((new / old) ** (1 / periods) - 1) * 100


def margin(numerator: float, denominator: float) -> float:
    return divide(numerator, denominator) * 100


def ratio(numerator: float, denominator: float) -> float:
    return divide(numerator, denominator)


CALCULATION_REGISTRY: dict[str, Callable[..., float]] = {
    "add": add,
    "subtract": subtract,
    "multiply": multiply,
    "divide": divide,
    "percentage_change": percentage_change,
    "cagr": cagr,
    "margin": margin,
    "ratio": ratio,
    "debt_ratio": ratio,
    "current_ratio": ratio,
}


def execute_operation(operation: str, arguments: list[float], *, periods: float | None = None) -> float:
    function = CALCULATION_REGISTRY.get(operation)
    if function is None:
        raise ValueError(f"Unsupported calculation operation: {operation}")
    if operation == "cagr":
        if len(arguments) != 2 or periods is None:
            raise ValueError("CAGR requires old, new, and periods")
        return function(arguments[0], arguments[1], periods)
    if len(arguments) != 2:
        raise ValueError(f"{operation} requires exactly two arguments")
    return function(arguments[0], arguments[1])
