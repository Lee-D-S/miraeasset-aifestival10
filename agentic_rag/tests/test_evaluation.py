import json
import unittest
from pathlib import Path

from agentic_rag.deterministic.metadata import detect_intent
from agentic_rag.deterministic.policy import policy_violation


class EvaluationScenarioTests(unittest.TestCase):
    def test_declared_scenarios_have_expected_routing(self):
        cases = json.loads((Path(__file__).parent / "eval_cases.json").read_text(encoding="utf-8"))
        for case in cases:
            if case["expected"] == "fallback":
                self.assertTrue(policy_violation(case["question"]))
            elif case["expected"] == "comparison":
                self.assertEqual(detect_intent(case["question"], {})[0], "comparison")
            elif case["expected"] == "calculation":
                self.assertEqual(detect_intent(case["question"], {})[0], "calculation")

