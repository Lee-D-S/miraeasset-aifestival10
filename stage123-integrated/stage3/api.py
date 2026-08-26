from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Support both ``python -m stage3.api`` and direct ``python stage3/api.py``
# execution when this folder is copied as a standalone package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stage3.service import Stage3Service
from stage3.adapters.stage1 import adapt_stage1_intent


Stage1Provider = Callable[[str], Mapping[str, Any]]
Stage2Provider = Callable[[Mapping[str, Any]], Any]


class Stage3Application:
    """stdlib-only HTTP boundary for the competition's GET /answer contract."""

    def __init__(
        self,
        *,
        stage1_provider: Stage1Provider,
        stage2_provider: Stage2Provider,
        answer_client: Any | None = None,
        execution_mode: str = "auto",
    ):
        self.stage1_provider = stage1_provider
        self.stage2_provider = stage2_provider
        self.service = Stage3Service(answer_client=answer_client, execution_mode=execution_mode)

    def answer(self, question_id: str, question: str) -> dict[str, str]:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                stage1_intent = self.stage1_provider(question)
                normalized_intent = adapt_stage1_intent(stage1_intent, question=question)
                stage2_result = {} if not normalized_intent.is_processable else self.stage2_provider(stage1_intent)
                return self.service.answer(question_id=question_id, question=question, stage1_intent=stage1_intent, stage2_result=stage2_result)
            except Exception as error:  # noqa: BLE001 - retry boundary
                last_error = error
        raise RuntimeError(f"Stage3 providers failed after 2 retries: {last_error}")

    def serve(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        application = self

        class Handler(BaseHTTPRequestHandler):
            def _json(self, status: int, payload: dict[str, Any]) -> None:
                body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
                parsed = urlparse(self.path)
                params = parse_qs(parsed.query, keep_blank_values=True)
                if parsed.path == "/health":
                    self._json(200, {"status": "ok", "implementation": "stage3"})
                    return
                if parsed.path != "/answer":
                    self._json(404, {"error": "not_found"})
                    return
                question_id = params.get("question_id", [""])[0]
                question = params.get("question", [""])[0]
                try:
                    self._json(200, application.answer(question_id, question))
                except Exception as error:  # noqa: BLE001 - API boundary
                    self._json(503, {"error": str(error)})

            def log_message(self, _format: str, *_args: Any) -> None:
                return

        ThreadingHTTPServer((host, port), Handler).serve_forever()


def create_app(
    *,
    stage1_provider: Stage1Provider,
    stage2_provider: Stage2Provider,
    answer_client: Any | None = None,
    execution_mode: str = "auto",
) -> Stage3Application:
    return Stage3Application(
        stage1_provider=stage1_provider,
        stage2_provider=stage2_provider,
        answer_client=answer_client,
        execution_mode=execution_mode,
    )
