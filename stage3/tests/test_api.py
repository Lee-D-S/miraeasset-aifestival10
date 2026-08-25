from __future__ import annotations

import unittest

from stage3.api import create_app


class ApiTests(unittest.TestCase):
    def test_exposes_health_and_answer_without_auth_header(self):
        app = create_app(
            stage1_provider=lambda question: {"question": question, "route": "need_clarify", "intent": "lookup"},
            stage2_provider=lambda intent: {},
        )
        routes = {route.path: route for route in app.routes}
        self.assertIn("/health", routes)
        self.assertIn("/answer", routes)
        self.assertIn("GET", routes["/answer"].methods)

    def test_provider_boundary_allows_two_retries(self):
        calls = {"count": 0}

        def failing_provider(_question):
            calls["count"] += 1
            raise RuntimeError("temporary failure")

        app = create_app(stage1_provider=failing_provider, stage2_provider=lambda intent: {})
        endpoint = next(route.endpoint for route in app.routes if route.path == "/answer")
        with self.assertRaises(Exception):
            endpoint("Q-001", "질문")
        self.assertEqual(calls["count"], 3)


if __name__ == "__main__":
    unittest.main()
