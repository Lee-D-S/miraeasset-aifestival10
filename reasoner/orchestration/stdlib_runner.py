from __future__ import annotations

from typing import Any

from reasoner.agents.answer import AnswerWriter
from reasoner.contracts import ReasonerFact, ReasonerIntent, ReasonerResult
from reasoner.node import build_reasoner_node
from reasoner.state import ReasonerGraphState


def run_stdlib(
    *,
    question: str,
    intent: ReasonerIntent,
    retriever_result: Any,
    answer_writer: AnswerWriter,
) -> ReasonerGraphState:
    """Run the canonical Reasoner node without constructing a LangGraph graph."""

    update = build_reasoner_node(answer_writer=answer_writer)({
        "question": question,
        "intent": intent,
        "route": intent.route,
        "retriever_result": retriever_result,
        "context": "",
        "messages": [],
        "gen_retry_num": 0,
    })
    result = ReasonerResult.from_dict(update["reasoner_result"])
    state: ReasonerGraphState = {
        "question": question,
        "intent": intent,
        "retriever_result": retriever_result,
        "facts": [ReasonerFact.from_dict(item) for item in result.facts],
        "calculations": list(result.calculations),
        "comparison_results": list(result.comparison_results),
        "linked_events": list(result.linked_events),
        "citations": list(result.citations),
        "warnings": list(result.warnings),
        "trace": list(result.trace),
        "answer": result.answer,
        "status": result.status,
        "reasoner_result": result.to_dict(),
        "fallback_used": False,
    }
    for key in ("context", "messages", "gen_retry_num"):
        if key in update:
            state[key] = update[key]  # type: ignore[literal-required]
    return state


__all__ = ["run_stdlib"]
