from __future__ import annotations

import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.calculation import calculate_facts
from stage3.agents.comparison import compare_facts
from stage3.agents.fact_extraction import extract_facts
from stage3.deterministic.normalization import normalize_facts


class CalculationComparisonTests(unittest.TestCase):
    def _facts(self, question: str, metric: str, documents: list[dict]):
        intent = adapt_stage1_intent({
            "raw_question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": "compare" if "기업" in question else "calc",
            "metric": metric,
            "basis": "연결",
            "time": {},
        })
        facts = extract_facts(adapt_stage2_bundle(documents).documents, intent)
        return intent, normalize_facts(facts, intent)[0]

    def test_percentage_change_uses_deterministic_registry(self):
        intent, facts = self._facts(
            "매출 증가율은?", "revenue",
            [
                {"id": "d1", "text": "2024년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"}},
                {"id": "d2", "text": "2025년 연결 매출액 120억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            ],
        )
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 20.0)
        self.assertEqual(result["evidence_ids"], ["d1", "d2"])

    def test_comparison_ranks_by_value_not_retrieval_score(self):
        intent, facts = self._facts(
            "기업A와 기업B 중 매출액이 큰 기업은?", "revenue",
            [
                {"id": "d1", "score": 0.99, "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "d2", "score": 0.10, "text": "매출액 120억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
            ],
        )
        result = compare_facts(facts, intent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["top"]["company"], "기업B")
        self.assertEqual(result["results"][1]["company"], "기업A")

    def test_mismatched_basis_is_rejected(self):
        intent, facts = self._facts(
            "매출 증가율은?", "revenue",
            [
                {"id": "d1", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"}},
                {"id": "d2", "text": "매출액 120억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "별도"}},
            ],
        )
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "invalid_basis")


if __name__ == "__main__":
    unittest.main()
