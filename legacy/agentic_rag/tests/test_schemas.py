import unittest

from agentic_rag.agents.schemas import validate_agent_result


class SchemaTests(unittest.TestCase):
    def test_valid_result(self):
        valid, _ = validate_agent_result({"agent": "x", "status": "ok", "confidence": 0.8, "evidence_ids": [], "trace": []})
        self.assertTrue(valid)

    def test_invalid_confidence_rejected(self):
        valid, _ = validate_agent_result({"agent": "x", "status": "ok", "confidence": 2, "evidence_ids": [], "trace": []})
        self.assertFalse(valid)

