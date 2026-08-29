from typing import Any


def percentage_change(old: float, new: float) -> float:
    if old == 0:
        raise ValueError("Cannot calculate percentage change from zero")
    return (new - old) / abs(old) * 100


def calculate(values: dict[str, Any]) -> dict[str, Any]:
    if "old" in values and "new" in values:
        old, new = float(values["old"]), float(values["new"])
        return {"old": old, "new": new, "percentage_change": percentage_change(old, new)}
    return {}

