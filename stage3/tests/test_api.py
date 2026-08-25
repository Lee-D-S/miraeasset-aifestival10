from __future__ import annotations

import unittest

from stage3.api import create_app


class ApiTests(unittest.TestCase):
    def test_creates_stdlib_application_without_external_web_framework(self):
        app = create_app(
            stage1_provider=lambda question: {"question": question, "route": "need_clarify", "intent": "lookup"},
            stage2_provider=lambda intent: {},
        )
        self.assertEqual(app.service.__class__.__name__, "Stage3Service")

    def test_provider_boundary_allows_two_retries(self):
        calls = {"count": 0}

        def failing_provider(_question):
            calls["count"] += 1
            raise RuntimeError("temporary failure")

        app = create_app(stage1_provider=failing_provider, stage2_provider=lambda intent: {})
        with self.assertRaises(Exception):
            app.answer("Q-001", "질문")
        self.assertEqual(calls["count"], 3)


if __name__ == "__main__":
    unittest.main()
