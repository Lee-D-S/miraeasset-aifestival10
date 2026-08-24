import time
import unittest

from agentic_rag.service import AgenticAnswerService


class SlowGraph:
    def invoke(self, *args, **kwargs):
        time.sleep(0.05)
        return {}


class OperationalTests(unittest.TestCase):
    def test_request_timeout_returns_fallback(self):
        service = AgenticAnswerService(retriever=type("R", (), {"corp_names": lambda self: [], "search": lambda self, q, l, f: []})(), reranker=lambda q, d: d, generator=lambda state: {})
        service.graph = SlowGraph()
        service.REQUEST_TIMEOUT_SECONDS = 0.001
        response = service.answer("TIMEOUT", "질문")
        self.assertIn("제한 시간을 초과", response.answer)

