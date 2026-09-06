from __future__ import annotations

import unittest

from reasoner.api import create_app


class ApiTests(unittest.TestCase):
    def test_creates_stdlib_application_without_external_web_framework(self):
        app = create_app(
            interpreter_provider=lambda question: {"question": question, "route": "need_clarify", "intent": "lookup"},
            retriever_provider=lambda intent: {},
            execution_mode="stdlib",
        )
        self.assertEqual(app.service.__class__.__name__, "ReasonerService")
        self.assertEqual(app.service.execution_mode, "stdlib")

    def test_invalid_execution_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            create_app(
                interpreter_provider=lambda question: {"question": question, "route": "need_clarify", "intent": "lookup"},
                retriever_provider=lambda intent: {},
                execution_mode="unknown",
            )

    def test_non_ok_route_does_not_call_retriever_provider(self):
        calls = {"count": 0}

        def retriever_provider(_intent):
            calls["count"] += 1
            raise AssertionError("Retriever must not run for a non-processable Interpreter route")

        app = create_app(
            interpreter_provider=lambda question: {
                "raw_question": question,
                "normalized_question": question,
                "route": "need_clarify",
                "intent": "unknown",
                "reject_reason": "ambiguous_corp",
                "clarify_message": "기업을 확인해 주세요.",
            },
            retriever_provider=retriever_provider,
        )

        response = app.answer("Q-CLARIFY", "삼성의 매출은?")

        self.assertEqual(calls["count"], 0)
        self.assertEqual(response["question_id"], "Q-CLARIFY")
        self.assertEqual(response["answer"], "")

    def test_provider_boundary_allows_two_retries(self):
        calls = {"count": 0}

        def failing_provider(_question):
            calls["count"] += 1
            raise RuntimeError("temporary failure")

        app = create_app(interpreter_provider=failing_provider, retriever_provider=lambda intent: {})
        with self.assertRaises(Exception):
            app.answer("Q-001", "질문")
        self.assertEqual(calls["count"], 3)


if __name__ == "__main__":
    unittest.main()
