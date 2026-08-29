from __future__ import annotations

from typing import Any

from stage3.agents.answer import AnswerWriter
from stage3.contracts import Stage3Fact, Stage3Intent, Stage3Result
from stage3.node import build_stage3_node
from stage3.state import Stage3GraphState


def run_stdlib(
    *,
    question: str,
    intent: Stage3Intent,
    stage2_result: Any,
    answer_writer: AnswerWriter,
) -> Stage3GraphState:
    """Run the canonical Stage3 node without constructing a LangGraph graph."""

    update = build_stage3_node(answer_writer=answer_writer)({
        "question": question,
        "intent": intent,
        "route": intent.route,
        "stage2_result": stage2_result,
        "context": "",
        "messages": [],
        "gen_retry_num": 0,
    })
    result = Stage3Result.from_dict(update["stage3_result"])
    state: Stage3GraphState = {
        "question": question,
        "intent": intent,
        "stage2_result": stage2_result,
        "facts": [Stage3Fact.from_dict(item) for item in result.facts],
        "calculations": list(result.calculations),
        "comparison_results": list(result.comparison_results),
        "linked_events": list(result.linked_events),
        "citations": list(result.citations),
        "warnings": list(result.warnings),
        "trace": list(result.trace),
        "answer": result.answer,
        "status": result.status,
        "stage3_result": result.to_dict(),
        "fallback_used": False,
    }
    for key in ("context", "messages", "gen_retry_num"):
        if key in update:
            state[key] = update[key]  # type: ignore[literal-required]
    return state


__all__ = ["run_stdlib"]
