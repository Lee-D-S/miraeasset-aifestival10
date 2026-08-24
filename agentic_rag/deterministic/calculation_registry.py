from __future__ import annotations

from collections.abc import Callable
from statistics import median
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


def power(left: float, right: float) -> float:
    return left**right


def average(*values: float) -> float:
    if not values:
        raise ValueError("average requires at least one value")
    return sum(values) / len(values)


def sum_values(*values: float) -> float:
    return sum(values)


def minimum(*values: float) -> float:
    if not values:
        raise ValueError("min requires at least one value")
    return min(values)


def maximum(*values: float) -> float:
    if not values:
        raise ValueError("max requires at least one value")
    return max(values)


def median_value(*values: float) -> float:
    if not values:
        raise ValueError("median requires at least one value")
    return float(median(values))


def greater_than(left: float, right: float) -> float:
    return float(left > right)


def less_than(left: float, right: float) -> float:
    return float(left < right)


def difference(left: float, right: float) -> float:
    return left - right


def threshold(value: float, limit: float) -> float:
    return float(value >= limit)


def rank_value(value: float, *population: float) -> float:
    if not population:
        raise ValueError("rank requires a population")
    return float(1 + sum(item > value for item in population))


def period_change(old: float, new: float) -> float:
    return percentage_change(old, new)


def margin(numerator: float, denominator: float) -> float:
    return divide(numerator, denominator) * 100


def ratio(numerator: float, denominator: float) -> float:
    return divide(numerator, denominator)


CALCULATION_REGISTRY: dict[str, Callable[..., float]] = {
    "add": add,
    "subtract": subtract,
    "multiply": multiply,
    "divide": divide,
    "power": power,
    "percentage_change": percentage_change,
    "cagr": cagr,
    "sum": sum_values,
    "average": average,
    "median": median_value,
    "min": minimum,
    "max": maximum,
    "greater_than": greater_than,
    "less_than": less_than,
    "difference": difference,
    "threshold": threshold,
    "rank": rank_value,
    "period_change": period_change,
    "rolling_average": average,
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
    if operation in {"sum", "average", "median", "min", "max", "rolling_average"}:
        if not arguments:
            raise ValueError(f"{operation} requires at least one argument")
        return function(*arguments)
    if operation == "rank":
        if len(arguments) < 2:
            raise ValueError("rank requires a value and population")
        return function(arguments[0], *arguments[1:])
    if len(arguments) != 2:
        raise ValueError(f"{operation} requires exactly two arguments")
    return function(arguments[0], arguments[1])
