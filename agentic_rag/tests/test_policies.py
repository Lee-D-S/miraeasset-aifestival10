import unittest

from agentic_rag.deterministic.evidence import validate_agent_outputs, validate_answer_claims
from agentic_rag.infrastructure.retry import retry_call
from agentic_rag.llm.history import HistoryPolicy


class PolicyTests(unittest.TestCase):
    def test_minimal_and_full_history(self):
        messages = [{"content": str(index)} for index in range(10)]
        self.assertEqual(len(HistoryPolicy("minimal", max_messages=3).select(messages)), 3)
        self.assertEqual(len(HistoryPolicy("full").select(messages)), 10)

    def test_provenance_validation_rejects_unknown_evidence(self):
        valid, reason = validate_agent_outputs([{"id": "known"}], [{"agent": "fact_extractor", "status": "ok", "confidence": 0.8, "evidence_ids": ["missing"], "trace": []}], [{"agent": "fact_extractor"}])
        self.assertFalse(valid)
        self.assertIn("근거", reason)

    def test_retry_retries_then_succeeds(self):
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("temporary")
            return "ok"

        self.assertEqual(retry_call(operation, sleep=lambda _: None), "ok")
        self.assertEqual(len(attempts), 3)

    def test_answer_claims_require_grounded_numbers_and_sources(self):
        documents = [{"id": "1", "source": "doc.pdf", "text": "매출은 100입니다."}]
        self.assertTrue(validate_answer_claims("[출처: doc.pdf] 매출은 100입니다.", documents)[0])
        self.assertFalse(validate_answer_claims("[출처: other.pdf] 매출은 999입니다.", documents)[0])
