from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from fastapi import FastAPI, HTTPException

from stage3.service import Stage3Service


Stage1Provider = Callable[[str], Mapping[str, Any]]
Stage2Provider = Callable[[Mapping[str, Any]], Any]


def create_app(*, stage1_provider: Stage1Provider, stage2_provider: Stage2Provider, answer_client: Any | None = None) -> FastAPI:
    """Build the competition endpoint without fixing Stage2's final contract."""

    app = FastAPI(title="Stage3 Disclosure Answer API")
    service = Stage3Service(answer_client=answer_client)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "implementation": "stage3"}

    @app.get("/answer")
    def answer(question_id: str, question: str) -> dict[str, str]:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                stage1_intent = stage1_provider(question)
                stage2_result = stage2_provider(stage1_intent)
                return service.answer(
                    question_id=question_id,
                    question=question,
                    stage1_intent=stage1_intent,
                    stage2_result=stage2_result,
                )
            except Exception as error:  # noqa: BLE001 - API boundary must retry provider failures.
                last_error = error
        raise HTTPException(status_code=503, detail=f"Stage3 providers failed after 2 retries: {last_error}")

    return app
