"""Competition HTTP boundary for the four-stage pipeline."""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException

from integration.service import StagePipeline


def _retrieved_context(state: dict[str, Any]) -> str:
    result = state.get("stage3_result") or {}
    citations = result.get("citations", [])
    if not isinstance(citations, list):
        return ""
    return "\n\n".join(
        f"[출처: {item.get('source', '')}][문서ID: {item.get('document_id', '')}]\n"
        f"{item.get('evidence', item.get('text', ''))}"
        for item in citations
        if isinstance(item, dict)
    )


def _think_trace(state: dict[str, Any]) -> str:
    trace: dict[str, Any] = {}
    for key in ("intent", "stage2_result", "stage3_result", "stage4_result"):
        value = state.get(key)
        if isinstance(value, dict):
            trace[key] = {
                field: value.get(field)
                for field in ("status", "warnings", "trace")
                if field in value
            }
    intent = state.get("intent")
    if isinstance(intent, dict) and intent.get("think_trace"):
        trace["stage1_think_trace"] = str(intent["think_trace"])
    trace["supervisor"] = {
        "phase": state.get("phase"),
        "action": state.get("supervisor_action"),
        "reason": state.get("supervisor_reason"),
        "search_attempts": state.get("search_attempts", 0),
        "planner_attempts": state.get("planner_attempts", 0),
        "regeneration_attempts": state.get("regeneration_attempts", 0),
        "validation_attempts": state.get("validation_attempts", 0),
        "termination_reason": state.get("termination_reason"),
    }
    return json.dumps(trace, ensure_ascii=False)


def to_submission_response(state: dict[str, Any]) -> dict[str, str]:
    """Convert final State to the competition's five-string response shape."""

    response = {
        "question_id": str(state["question_id"]),
        "question": str(state["question"]),
        "retrieved_context": _retrieved_context(state),
        "think_trace": _think_trace(state),
        "answer": str(state.get("answer") or ""),
    }
    return response


def create_app(pipeline: StagePipeline | None = None) -> FastAPI:
    """Create the public app; Stage implementations are injected later."""

    app = FastAPI(title="AI Festival Four-Stage Agent", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "implementation": "four-stage"}

    @app.get("/answer")
    def answer(question_id: str, question: str) -> dict[str, str]:
        if pipeline is None:
            raise HTTPException(status_code=503, detail="Stage1~Stage4 구현이 아직 연결되지 않았습니다.")
        try:
            state = pipeline.invoke(question_id=question_id, question=question)
        except Exception as error:  # noqa: BLE001 - HTTP boundary
            raise HTTPException(status_code=503, detail=str(error)) from error
        return to_submission_response(state)

    return app


__all__ = ["create_app", "to_submission_response"]
