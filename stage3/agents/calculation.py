from __future__ import annotations

from stage3.contracts import AgentResult, Stage3Fact
from stage3.deterministic.calculation_planner import SUPPORTED_OPERATIONS, build_calculation_plan
from stage3.deterministic.calculations import calculate_facts
from stage3.state import Stage3GraphState


def calculation_agent(state: Stage3GraphState) -> AgentResult:
    intent = state["intent"]
    facts = [
        fact if isinstance(fact, Stage3Fact) else Stage3Fact.from_dict(fact)
        for fact in state.get("facts", [])
    ]
    plan = build_calculation_plan(intent)
    if not plan or plan.get("operation") not in SUPPORTED_OPERATIONS:
        return AgentResult(
            agent="calculation",
            status="unsupported",
            confidence=0.0,
            trace=("plan=none",),
        )

    result = calculate_facts(facts, intent, operation=plan["operation"])
    evidence_ids = tuple(str(item) for item in result.get("evidence_ids", []) if item)
    status = str(result.get("status", "insufficient_evidence"))
    warnings = (str(result["error"]),) if result.get("error") else ()
    return AgentResult(
        agent="calculation",
        status=status,
        calculations=(dict(result),),
        evidence_ids=evidence_ids,
        confidence=1.0 if status == "ok" else 0.0,
        warnings=warnings,
        trace=(f"operation={plan['operation']}", f"status={status}"),
    )


__all__ = ["calculate_facts", "calculation_agent"]
