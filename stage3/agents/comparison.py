from __future__ import annotations

from typing import Any, Iterable

from stage3.contracts import Stage3Fact, Stage3Intent
from stage3.deterministic.calculations import calculate_facts


def compare_facts(facts: Iterable[Stage3Fact], intent: Stage3Intent) -> dict[str, Any]:
    """Compare normalized facts by extracted value, never by retrieval score."""

    result = calculate_facts(facts, intent, operation="rank")
    if result.get("status") == "ok":
        result["explanation_inputs"] = [
            {"company": item["company"], "value": item["value"], "document_id": item["document_id"]}
            for item in result.get("results", [])
        ]
    return result
