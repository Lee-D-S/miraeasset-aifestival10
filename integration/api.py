"""Competition HTTP boundary for the four-stage pipeline."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from threading import Lock
from typing import Any

from fastapi import FastAPI, HTTPException

from integration.service import StagePipeline


logger = logging.getLogger(__name__)

_TRACE_SENSITIVE_KEYS = frozenset({"api_key", "authorization", "prompt", "messages", "content", "question"})


def _redact_trace(value: Any, *, depth: int = 0) -> Any:
    """Keep think_trace JSON-like and bounded without echoing secrets/prompts."""

    if depth > 4:
        return "[truncated]"
    if isinstance(value, dict):
        return {
            str(key): _redact_trace(item, depth=depth + 1)
            for key, item in value.items()
            if str(key).lower() not in _TRACE_SENSITIVE_KEYS
        }
    if isinstance(value, (list, tuple)):
        return [_redact_trace(item, depth=depth + 1) for item in list(value)[:32]]
    if isinstance(value, str):
        return value[:500]
    return value


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
                for field in ("status", "warnings", "trace", "provider_status", "failure_reason_code")
                if field in value
            }
            subresults = value.get("subresults")
            if isinstance(subresults, list):
                trace[key]["subqueries"] = [
                    {
                        "subquery_id": item.get("subquery_id"),
                        "status": item.get("status"),
                    }
                    for item in subresults
                    if isinstance(item, dict)
                ]
    intent = state.get("intent")
    if isinstance(intent, dict) and intent.get("think_trace"):
        trace["stage1_think_trace"] = str(intent["think_trace"])
    plan = state.get("analysis_plan")
    if isinstance(plan, dict):
        trace["analysis_plan"] = {
            "status": state.get("plan_status") or plan.get("status"),
            "failure_reason": state.get("plan_failure_reason"),
            "trace": state.get("plan_trace", []),
            "requirements": len(plan.get("requirements", [])) if isinstance(plan.get("requirements"), list) else 0,
            "steps": len(plan.get("steps", [])) if isinstance(plan.get("steps"), list) else 0,
        }
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
    return json.dumps(_redact_trace(trace), ensure_ascii=False)


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


def create_app(
    pipeline: StagePipeline | None = None,
    *,
    pipeline_factory: Callable[[], StagePipeline] | None = None,
) -> FastAPI:
    """Create the public app without requiring pipeline startup at import time.

    ``pipeline`` keeps the direct-injection path used by tests.  Production can
    pass ``pipeline_factory`` so corpus, database, and provider initialization
    is delayed until readiness or the first answer request.
    """

    if pipeline is not None and pipeline_factory is not None:
        raise ValueError("provide either pipeline or pipeline_factory, not both")

    current_pipeline = pipeline
    pipeline_error: Exception | None = None
    pipeline_lock = Lock()

    def get_pipeline() -> StagePipeline:
        nonlocal current_pipeline, pipeline_error
        if current_pipeline is not None:
            return current_pipeline
        if pipeline_factory is None:
            raise RuntimeError("pipeline is not configured")
        with pipeline_lock:
            if current_pipeline is not None:
                return current_pipeline
            try:
                current_pipeline = pipeline_factory()
                pipeline_error = None
            except Exception as error:  # noqa: BLE001 - boundary stores startup failure
                pipeline_error = error
                logger.exception("pipeline initialization failed")
                raise
        return current_pipeline

    app = FastAPI(title="AI Festival Four-Stage Agent", version="0.1.0")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "implementation": "four-stage"}

    @app.get("/ready")
    def ready() -> dict[str, str]:
        """Report whether the executable pipeline can accept answer requests."""

        try:
            get_pipeline()
        except Exception as error:  # noqa: BLE001 - HTTP boundary
            detail = "pipeline is not ready"
            if pipeline_error is not None:
                logger.debug("readiness failure: %s", error)
            raise HTTPException(status_code=503, detail=detail) from error
        return {"status": "ready", "implementation": "four-stage"}

    @app.get("/answer")
    def answer(question_id: str, question: str) -> dict[str, str]:
        try:
            state = get_pipeline().invoke(question_id=question_id, question=question)
        except Exception as error:  # noqa: BLE001 - HTTP boundary
            if current_pipeline is None:
                raise HTTPException(status_code=503, detail="pipeline is not ready") from error
            logger.exception("pipeline request failed")
            raise HTTPException(status_code=503, detail="pipeline request failed") from error
        return to_submission_response(state)

    return app


__all__ = ["create_app", "to_submission_response"]
