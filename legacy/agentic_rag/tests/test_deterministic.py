import unittest

from agentic_rag.deterministic.calculations import percentage_change
from agentic_rag.deterministic.metadata import detect_intent, normalize_question
from agentic_rag.deterministic.policy import policy_violation


class DeterministicTests(unittest.TestCase):
    def test_normalization_and_intent(self):
        self.assertEqual(normalize_question("  삼성전자\u200b  2023년  "), "삼성전자 2023년")
        self.assertEqual(detect_intent("두 기업 비교", {})[0], "comparison")

    def test_policy_and_calculation(self):
        self.assertTrue(policy_violation("주가 예측해줘"))
        self.assertAlmostEqual(percentage_change(100, 120), 20.0)
        with self.assertRaises(ValueError):
            percentage_change(0, 1)

