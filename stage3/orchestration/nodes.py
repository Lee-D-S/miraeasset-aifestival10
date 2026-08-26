from __future__ import annotations

from typing import Any, Callable

from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.answer import AnswerWriter, make_answer_agent
from stage3.agents.calculation import calculation_agent
from stage3.agents.comparison import comparison_agent
from stage3.agents.event_linker import event_linker_agent
from stage3.agents.fact_extraction import fact_extraction_agent
from stage3.contracts import AgentResult, HandoffRequest, Provenance, Stage3Document, Stage3Fact, Stage3Intent, Stage3Result
from stage3.agents.schemas import validate_agent_result
from stage3.state import Stage3GraphState
from stage3.validation import validate_stage3_result

from stage3.orchestration.router import analysis_targets


def _provenance(result: AgentResult, state: Stage3GraphState) -> dict[str, Any]:
    by_id = {document.id: document for document in state.get("documents", [])}
    sources = tuple(by_id[item].source for item in result.evidence_ids if item in by_id)
    return Provenance(
        agent=result.agent,
        question=state.get("question", ""),
        document_ids=result.evidence_ids,
        sources=tuple(dict.fromkeys(sources)),
        confidence=result.confidence,
        details={"status": result.status},
    ).to_dict()


def _agent_update(result: AgentResult, state: Stage3GraphState, **values: Any) -> dict[str, Any]:
    return {
        **values,
        "agent_results": [result.to_dict()],
        "provenance": [_provenance(result, state)],
        "warnings": list(result.warnings),
        "trace": list(result.trace),
    }


def stage1_gate_node(state: Stage3GraphState) -> dict[str, Any]:
    intent = state["intent"]
    result = AgentResult(
        agent="supervisor",
        status="ok" if intent.is_processable else "blocked",
        evidence_ids=(),
        confidence=1.0,
        trace=(f"route={intent.route}",),
    )
    values = _agent_update(result, state, trace=[f"route={intent.route}"])
    if intent.is_processable:
        return values
    return {
        **values,
        "status": intent.route,
        "fallback_reason": intent.route,
    }


def blocked_response_node(state: Stage3GraphState) -> dict[str, Any]:
    route = state["intent"].route
    answer = {
        "need_clarify": "질문의 기업·기간·기준이 명확하지 않습니다.",
        "unanswerable": "제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.",
        "unsafe": "공시 근거만으로 답변할 수 없는 요청입니다.",
    }.get(route, "제공된 공시에서 확인할 수 없습니다.")
    return {"answer": answer, "answer_mode": "blocked", "status": route, "trace": ["blocked_response"]}


def stage2_adapter_node(state: Stage3GraphState) -> dict[str, Any]:
    bundle = adapt_stage2_bundle(state.get("stage2_result", {}))
    documents = bundle.effective_documents()
    warnings: list[str] = []
    for document in documents:
        file_format = str(document.metadata.get("file_format", "")).lower()
        span_text = any(
            str(span.get("text", span.get("content", span.get("evidence", "")))).strip()
            for span in document.evidence_spans
        )
        if "pdf" in file_format and not document.text.strip() and not span_text:
            warnings.append(f"{document.id}: pdf_text_required")
    return {
        "documents": documents,
        "warnings": warnings,
        "trace": [f"documents={len(documents)}"],
    }


def fact_extraction_node(state: Stage3GraphState) -> dict[str, Any]:
    result = fact_extraction_agent(state)
    facts = [Stage3Fact.from_dict(item) for item in result.facts]
    return _agent_update(result, state, facts=facts)


def analysis_router_node(state: Stage3GraphState) -> dict[str, Any]:
    targets = analysis_targets(state["intent"])
    handoffs = [
        HandoffRequest(
            source="supervisor",
            target=target.removesuffix("_agent"),
            task=target,
            reason=f"intent={state['intent'].intent}",
            evidence=tuple(fact.document_id for fact in state.get("facts", [])),
            trace=("stage3_supervisor",),
        ).to_dict()
        for target in targets
    ]
    result = AgentResult(
        agent="supervisor",
        status="ok",
        evidence_ids=tuple(fact.document_id for fact in state.get("facts", []) if fact.document_id),
        confidence=1.0,
        trace=(f"analysis_targets={','.join(targets) or 'answer'}",),
    )
    return {
        **_agent_update(result, state),
        "handoffs": handoffs,
    }


def make_specialist_node(handler: Callable[[Stage3GraphState], AgentResult], output_key: str):
    def node(state: Stage3GraphState) -> dict[str, Any]:
        result = handler(state)
        values: dict[str, Any] = {output_key: list(getattr(result, output_key))}
        return _agent_update(result, state, **values)

    return node


def merge_analysis_node(state: Stage3GraphState) -> dict[str, Any]:
    facts = state.get("facts", [])
    calculations = state.get("calculations", [])
    comparisons = state.get("comparison_results", [])
    events = state.get("linked_events", [])
    used: set[str] = {fact.document_id for fact in facts}
    for item in calculations:
        used.update(map(str, item.get("evidence_ids", [])))
    for item in comparisons:
        used.update(map(str, item.get("evidence_ids", [])))
    for item in events:
        used.update(map(str, item.get("source_ids", [])))
    evidence_by_id: dict[str, list[str]] = {}
    for fact in facts:
        if fact.document_id in used and fact.evidence:
            evidence_by_id.setdefault(fact.document_id, []).append(fact.evidence)
    for document in state.get("documents", []):
        if document.id not in used:
            continue
        for span in document.evidence_spans:
            value = span.get("text", span.get("content", span.get("evidence", "")))
            if str(value).strip():
                evidence_by_id.setdefault(document.id, []).append(str(value).strip())
    citations: list[dict[str, Any]] = []
    for document in state.get("documents", []):
        if document.id not in used:
            continue
        snippets = list(dict.fromkeys(evidence_by_id.get(document.id, [])))
        citations.append({
            "document_id": document.id,
            "source": document.source,
            "evidence": "\n".join(snippets) if snippets else document.text,
            "metadata": document.metadata,
        })
    return {"citations": citations, "trace": [f"citations={len(citations)}"]}


def answer_node(writer: AnswerWriter):
    handler = make_answer_agent(writer)

    def node(state: Stage3GraphState) -> dict[str, Any]:
        result = handler(state)
        mode = result.trace[0].removeprefix("mode=") if result.trace else "unknown"
        return _agent_update(result, state, answer=result.answer, answer_mode=mode)

    return node


def _state_result(state: Stage3GraphState) -> Stage3Result:
    calculations = list(state.get("calculations", []))
    comparisons = list(state.get("comparison_results", []))
    failed_calculation = bool(calculations) and not any(item.get("status") == "ok" for item in calculations)
    failed_comparison = bool(comparisons) and not any(item.get("status") == "ok" for item in comparisons)
    usable_analysis = bool(state.get("facts") or state.get("linked_events") or any(item.get("status") == "ok" for item in calculations + comparisons))
    status = state.get("status") or ("success" if state.get("documents") and usable_analysis and not failed_calculation and not failed_comparison else "insufficient_evidence")
    return Stage3Result(
        status=status,
        answer=state.get("answer", ""),
        facts=[fact.to_dict() for fact in state.get("facts", [])],
        calculations=calculations,
        comparison_results=comparisons,
        linked_events=list(state.get("linked_events", [])),
        citations=list(state.get("citations", [])),
        warnings=[
            *state.get("warnings", []),
            *state.get("validation", {}).get("warnings", []),
        ],
        provenance=list(state.get("provenance", [])),
        trace=list(state.get("trace", [])),
        agent_results=list(state.get("agent_results", [])),
        handoffs=list(state.get("handoffs", [])),
    )


def validation_node(state: Stage3GraphState) -> dict[str, Any]:
    result = _state_result(state)
    valid, warnings = validate_stage3_result(result, state["intent"])
    document_ids = {document.id for document in state.get("documents", [])}
    agent_warnings: list[str] = []
    agent_results = list(state.get("agent_results", []))
    provenance_agents = {str(item.get("agent", "")) for item in state.get("provenance", [])}
    for payload in agent_results:
        schema_valid, schema_message = validate_agent_result(payload)
        if not schema_valid:
            agent_warnings.append(schema_message)
        if str(payload.get("agent", "")) not in provenance_agents:
            agent_warnings.append(f"Agent provenance가 없습니다: {payload.get('agent', '')}")
        unknown_ids = set(map(str, payload.get("evidence_ids", []))) - document_ids
        if unknown_ids:
            agent_warnings.append(f"Agent가 알 수 없는 문서를 참조합니다: {sorted(unknown_ids)}")
    warnings = [*warnings, *agent_warnings]
    validator = AgentResult(
        agent="validator",
        status="valid" if not warnings else "invalid",
        evidence_ids=tuple(sorted(document_ids & {str(item.get("document_id", "")) for item in state.get("citations", [])})),
        confidence=1.0 if not warnings else 0.0,
        warnings=tuple(warnings),
        trace=("validation=passed" if not warnings else "validation=failed",),
    )
    return {
        "validation": {"valid": not warnings, "warnings": warnings},
        "status": result.status if not warnings else "insufficient_evidence",
        "agent_results": [validator.to_dict()],
        "provenance": [_provenance(validator, state)],
        "trace": ["validated" if not warnings else "validation_failed"],
    }


def fallback_node(writer: AnswerWriter):
    def node(state: Stage3GraphState) -> dict[str, Any]:
        facts = [fact if isinstance(fact, Stage3Fact) else Stage3Fact.from_dict(fact) for fact in state.get("facts", [])]
        answer = writer.deterministic(
            intent=state["intent"],
            facts=facts,
            calculations=list(state.get("calculations", [])),
            comparisons=list(state.get("comparison_results", [])),
            events=list(state.get("linked_events", [])),
            citations=list(state.get("citations", [])),
            warnings=list(state.get("warnings", [])),
        )
        result = AgentResult(
            agent="fallback",
            status="ok" if answer.strip() else "empty",
            answer=answer,
            evidence_ids=tuple(str(item.get("document_id", "")) for item in state.get("citations", []) if item.get("document_id")),
            confidence=0.8 if answer.strip() else 0.0,
            trace=("mode=deterministic_fallback",),
        )
        return _agent_update(result, state, answer=answer, answer_mode="deterministic_fallback", fallback_used=True)

    return node


def final_failure_node(state: Stage3GraphState) -> dict[str, Any]:
    return {
        "answer": "제공된 공시 근거만으로 답변을 검증할 수 없습니다.",
        "status": "insufficient_evidence",
        "trace": ["fallback_validation_failed"],
    }


def state_to_result(state: Stage3GraphState) -> Stage3Result:
    return _state_result(state)


__all__ = [
    "analysis_router_node",
    "answer_node",
    "blocked_response_node",
    "fact_extraction_node",
    "fallback_node",
    "final_failure_node",
    "make_specialist_node",
    "merge_analysis_node",
    "stage1_gate_node",
    "stage2_adapter_node",
    "state_to_result",
    "validation_node",
]
