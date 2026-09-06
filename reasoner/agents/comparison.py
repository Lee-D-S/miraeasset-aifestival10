from __future__ import annotations

from typing import Any, Iterable

from reasoner.contracts import AgentResult, ReasonerFact, ReasonerIntent
from reasoner.deterministic.calculations import calculate_facts
from reasoner.state import ReasonerGraphState


def compare_facts(facts: Iterable[ReasonerFact], intent: ReasonerIntent) -> dict[str, Any]:
    """Compare normalized facts by extracted value, never by retrieval score."""

    result = calculate_facts(facts, intent, operation="rank")
    if result.get("status") == "ok":
        result["explanation_inputs"] = [
            {"company": item["company"], "value": item["value"], "document_id": item["document_id"]}
            for item in result.get("results", [])
        ]
    return result


def comparison_agent(state: ReasonerGraphState) -> AgentResult:
    intent = state["intent"]
    facts = [
        fact if isinstance(fact, ReasonerFact) else ReasonerFact.from_dict(fact)
        for fact in state.get("facts", [])
    ]
    result = compare_facts(facts, intent)
    evidence_ids = tuple(str(item) for item in result.get("evidence_ids", []) if item)
    status = str(result.get("status", "insufficient_evidence"))
    warnings = (str(result["error"]),) if result.get("error") else ()
    return AgentResult(
        agent="comparison",
        status=status,
        comparison_results=(dict(result),),
        evidence_ids=evidence_ids,
        confidence=1.0 if status == "ok" else 0.0,
        warnings=warnings,
        trace=(f"status={status}", f"results={len(result.get('results', []))}"),
    )
