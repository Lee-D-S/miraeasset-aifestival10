from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Support both ``python -m reasoner.api`` and direct ``python reasoner/api.py``
# execution when this folder is copied as a standalone package.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from reasoner.service import ReasonerService
from reasoner.adapters.interpreter import adapt_interpreter_intent


InterpreterProvider = Callable[[str], Mapping[str, Any]]
RetrieverProvider = Callable[[Mapping[str, Any]], Any]


class ReasonerApplication:
    """stdlib-only HTTP boundary for the competition's GET /answer contract."""

    def __init__(
        self,
        *,
        interpreter_provider: InterpreterProvider,
        retriever_provider: RetrieverProvider,
        answer_client: Any | None = None,
        execution_mode: str = "auto",
    ):
        self.interpreter_provider = interpreter_provider
        self.retriever_provider = retriever_provider
        self.service = ReasonerService(answer_client=answer_client, execution_mode=execution_mode)

    def answer(self, question_id: str, question: str) -> dict[str, str]:
        last_error: Exception | None = None
        for _attempt in range(3):
            try:
                interpreter_intent = self.interpreter_provider(question)
                normalized_intent = adapt_interpreter_intent(interpreter_intent, question=question)
                retriever_result = {} if not normalized_intent.is_processable else self.retriever_provider(interpreter_intent)
                return self.service.answer(question_id=question_id, question=question, interpreter_intent=interpreter_intent, retriever_result=retriever_result)
            except Exception as error:  # noqa: BLE001 - retry boundary
                last_error = error
        raise RuntimeError(f"Reasoner providers failed after 2 retries: {last_error}")

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
                    self._json(200, {"status": "ok", "implementation": "reasoner"})
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
    interpreter_provider: InterpreterProvider,
    retriever_provider: RetrieverProvider,
    answer_client: Any | None = None,
    execution_mode: str = "auto",
) -> ReasonerApplication:
    return ReasonerApplication(
        interpreter_provider=interpreter_provider,
        retriever_provider=retriever_provider,
        answer_client=answer_client,
        execution_mode=execution_mode,
    )
