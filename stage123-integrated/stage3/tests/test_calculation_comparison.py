from __future__ import annotations

import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.calculation import calculate_facts
from stage3.agents.comparison import compare_facts
from stage3.agents.fact_extraction import extract_facts
from stage3.contracts import Stage3Fact
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

    def test_percentage_change_does_not_use_unrequested_last_periods(self):
        intent = adapt_stage1_intent({
            "raw_question": "2024년에서 2025년 매출 증가율은?",
            "normalized_question": "2024년에서 2025년 매출 증가율은?",
            "route": "ok",
            "intent": "calc",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2024, 2025], "base_months": [12]},
        })
        facts = self._facts(
            intent.question,
            "revenue",
            [
                {"id": "old", "text": "2023년 연결 매출액 80억원", "metadata": {"corp_name": "기업A", "basis": "연결"}},
                {"id": "new", "text": "2024년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "basis": "연결"}},
            ],
        )[1]
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_comparison_requires_same_period_for_all_companies(self):
        intent, facts = self._facts(
            "기업A와 기업B 중 매출액이 큰 기업은?", "revenue",
            [
                {"id": "a", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "b", "text": "매출액 120억원", "metadata": {"corp_name": "기업B", "report_period": "2024-12", "basis": "연결"}},
            ],
        )
        result = compare_facts(facts, intent)
        self.assertEqual(result["status"], "invalid_period")

    def test_sector_comparison_requires_every_stage1_sector_member(self):
        intent = adapt_stage1_intent({
            "raw_question": "2차전지 기업 중 설비투자가 가장 큰 곳은?",
            "normalized_question": "2차전지 기업 중 설비투자가 가장 큰 곳은?",
            "route": "ok",
            "intent": "compare",
            "metric": "capex",
            "basis": "연결",
            "sector_members": ["기업A", "기업B", "기업C"],
            "time": {"years": [2025], "base_months": [12]},
        })
        facts = self._facts(
            intent.question,
            "capex",
            [
                {"id": "a", "text": "설비투자 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "b", "text": "설비투자 120억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
            ],
        )[1]
        result = compare_facts(facts, intent)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["missing_companies"], ["기업C"])

    def test_ratio_percent_uses_different_metric_facts(self):
        intent = adapt_stage1_intent({
            "raw_question": "설비투자 매출 비중은?",
            "normalized_question": "설비투자 매출 비중은?",
            "route": "ok",
            "intent": "calc",
            "metric": "capex",
            "basis": "연결",
        })
        facts = extract_facts(adapt_stage2_bundle([
            {"id": "capex", "text": "설비투자 10억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "revenue", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]).documents, intent)
        facts = normalize_facts(facts, intent)[0]
        result = calculate_facts(facts, intent, operation="ratio_percent")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 10.0)

    def test_currency_mismatch_is_rejected(self):
        def fact(document_id: str, currency: str) -> Stage3Fact:
            return Stage3Fact(
                metric="revenue", label="매출액", value=100.0, raw_value="100", unit="원",
                normalized_value=100.0, period="2025-12", basis="연결", company=document_id,
                document_id=document_id, source="", evidence="매출액", currency=currency,
            )
        intent = adapt_stage1_intent({"route": "ok", "intent": "compare", "metric": "revenue", "basis": "연결"})
        result = compare_facts([fact("기업A", "KRW"), fact("기업B", "USD")], intent)
        self.assertEqual(result["status"], "invalid_currency")

    def test_convertible_krw_units_are_compared_after_normalization(self):
        intent, facts = self._facts(
            "기업A와 기업B 중 매출액이 큰 기업은?", "revenue",
            [
                {"id": "a", "text": "매출액 1억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "b", "text": "매출액 100백만원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
            ],
        )
        result = compare_facts(facts, intent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["top"]["unit"], "원")

    def test_margin_and_cagr_use_whitelisted_formulas(self):
        margin_intent = adapt_stage1_intent({
            "raw_question": "영업이익률은?", "normalized_question": "영업이익률은?", "route": "ok",
            "intent": "calc", "metric": "operating_profit", "basis": "연결",
        })
        margin_facts = extract_facts(adapt_stage2_bundle([
            {"id": "op", "text": "영업이익 20억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "sales", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]).documents, margin_intent)
        margin_result = calculate_facts(normalize_facts(margin_facts, margin_intent)[0], margin_intent)
        self.assertEqual(margin_result["result"], 20.0)

        cagr_intent = adapt_stage1_intent({
            "raw_question": "2023년부터 2025년까지 CAGR은?", "normalized_question": "2023년부터 2025년까지 CAGR은?", "route": "ok",
            "intent": "calc", "metric": "revenue", "basis": "연결",
            "time": {"years": [2023, 2025], "base_months": [12]},
        })
        cagr_facts = self._facts(
            cagr_intent.question, "revenue",
            [
                {"id": "start", "text": "2023년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2023-12", "basis": "연결"}},
                {"id": "end", "text": "2025년 연결 매출액 121억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            ],
        )[1]
        cagr_result = calculate_facts(cagr_facts, cagr_intent, operation="cagr")
        self.assertAlmostEqual(cagr_result["result"], 10.0, places=6)


if __name__ == "__main__":
    unittest.main()
