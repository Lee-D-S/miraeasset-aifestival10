from __future__ import annotations

import unittest

from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.adapters.retriever import adapt_retriever_bundle
from reasoner.agents.calculation import calculate_facts
from reasoner.agents.comparison import compare_facts
from reasoner.agents.fact_extraction import extract_facts
from reasoner.contracts import ReasonerFact
from reasoner.deterministic.calculation_planner import build_analysis_plan
from reasoner.deterministic.calculations import execute_analysis_plan
from reasoner.deterministic.normalization import normalize_facts


class CalculationComparisonTests(unittest.TestCase):
    def _facts(self, question: str, metric: str, documents: list[dict]):
        intent = adapt_interpreter_intent({
            "raw_question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": "compare" if "기업" in question else "calc",
            "question_type": "compare" if "기업" in question else "calculation",
            "calculation": {"operation": "rank"} if "기업" in question else {"operation": "percentage_change"},
            "metric": metric,
            "basis": "연결",
            "time": {},
        })
        facts = extract_facts(adapt_retriever_bundle(documents).documents, intent)
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

    def test_percentage_change_ignores_not_total_revenue_breakdown(self):
        intent = adapt_interpreter_intent({
            "raw_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "normalized_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "percentage_change"},
            "metric": "revenue",
            "basis": "연결",
            "companies": ["기업A"],
            "time": {"years": [2024, 2025], "base_months": [12]},
        })
        facts = extract_facts(adapt_retriever_bundle([
            {
                "id": "total-2024",
                "text": "2024년 연결 매출액 100억원",
                "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"},
            },
            {
                "id": "total-2025",
                "text": "2025년 연결 매출액 120억원",
                "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
            },
            {
                "id": "revenue-type-breakdown",
                "text": (
                    "| 구분 | 제56기(2024) | 제57기(2025) |\n"
                    "| --- | --- | --- |\n"
                    "| 용역 및 기타매출 | 75,092 | 100 |"
                ),
                "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
            },
        ]).documents, intent)
        facts = normalize_facts(facts, intent)[0]
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 20.0)
        self.assertEqual(result["evidence_ids"], ["total-2024", "total-2025"])
        input_values = [str(item.get("value", "")).replace(",", "") for item in result["inputs"]]
        self.assertFalse(any("75092" in value or value == "100" for value in input_values))

    def test_percentage_change_keeps_jo_total_when_million_won_breakdown_is_present(self):
        def amount(*, document_id: str, period: str, value: float, unit: str, scope: str, label: str) -> ReasonerFact:
            multiplier = {"조원": 1_000_000_000_000, "백만원": 1_000_000}[unit]
            return ReasonerFact(
                metric="revenue",
                label=label,
                value=value,
                raw_value=value,
                unit=unit,
                normalized_value=value * multiplier,
                period=period,
                basis="연결",
                company="기업A",
                document_id=document_id,
                source="",
                evidence=label,
                confidence=0.9,
                currency="KRW",
                aggregation_scope=scope,
            )

        intent = adapt_interpreter_intent({
            "raw_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "normalized_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "percentage_change"},
            "metric": "revenue",
            "basis": "연결",
            "companies": ["기업A"],
            "time": {"years": [2024, 2025], "base_months": [12]},
        })
        facts = [
            amount(document_id="total-2024", period="2024-12", value=300, unit="조원", scope="unknown", label="매출"),
            amount(document_id="total-2025", period="2025-12", value=330, unit="조원", scope="unknown", label="매출"),
            amount(document_id="breakdown-2024", period="2024-12", value=75_092, unit="백만원", scope="unknown", label="매출"),
            amount(document_id="breakdown-2025", period="2025-12", value=100, unit="백만원", scope="unknown", label="매출"),
        ]
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 10.0)
        self.assertEqual(result["evidence_ids"], ["total-2024", "total-2025"])
        input_values = [str(item.get("value", "")).replace(",", "") for item in result["inputs"]]
        self.assertFalse(any("75092" in value or value == "100" for value in input_values))

    def test_analysis_plan_percentage_change_drops_not_total_and_picks_period_totals(self):
        def amount(*, document_id: str, period: str, value: float, unit: str, scope: str, label: str) -> ReasonerFact:
            multiplier = {"조원": 1_000_000_000_000, "백만원": 1_000_000, "%": 1}[unit]
            return ReasonerFact(
                metric="revenue",
                label=label,
                value=value,
                raw_value=value,
                unit=unit,
                normalized_value=value * multiplier,
                period=period,
                basis="연결",
                company="기업A",
                document_id=document_id,
                source="",
                evidence=label,
                confidence=0.9,
                currency="KRW" if unit != "%" else None,
                aggregation_scope=scope,
            )

        intent = adapt_interpreter_intent({
            "raw_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "normalized_question": "삼성전자의 2024년과 2025년 매출액을 비교해줘",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "percentage_change", "metric": "revenue"},
            "metric": "revenue",
            "basis": "연결",
            "companies": ["기업A"],
            "time": {"years": [2024, 2025], "base_months": [12]},
        })
        plan = build_analysis_plan(intent)
        self.assertIsNotNone(plan)
        facts = [
            amount(document_id="total-2024", period="2024-12", value=300, unit="조원", scope="unknown", label="매출"),
            amount(document_id="total-2025", period="2025-12", value=330, unit="조원", scope="unknown", label="매출"),
            amount(document_id="breakdown-2024", period="2024-12", value=75_092, unit="백만원", scope="not_total", label="용역 및 기타매출"),
            amount(document_id="breakdown-2025", period="2025-12", value=100, unit="백만원", scope="not_total", label="용역 및 기타매출"),
            amount(document_id="share-2025", period="2025-12", value=100, unit="%", scope="total", label="매출액"),
            amount(document_id="noise-2024", period="2024-12", value=16, unit="백만원", scope="unknown", label="매출"),
        ]
        result = execute_analysis_plan(plan, facts, intent=intent)
        self.assertTrue(result["success"])
        calculation = result["calculations"][0]
        self.assertEqual(calculation["status"], "ok")
        self.assertEqual(calculation["result"], 10.0)
        self.assertEqual(calculation["evidence_ids"], ["total-2024", "total-2025"])
        input_values = [str(item.get("value", "")).replace(",", "") for item in calculation["inputs"]]
        self.assertFalse(any("75092" in value or value == "100" or value == "16" for value in input_values))

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

    def test_percentage_change_drops_facts_with_unrequested_basis(self):
        intent, facts = self._facts(
            "매출 증가율은?", "revenue",
            [
                {"id": "d1", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"}},
                {"id": "d2", "text": "매출액 120억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "별도"}},
            ],
        )
        result = calculate_facts(facts, intent)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_percentage_change_does_not_use_unrequested_last_periods(self):
        intent = adapt_interpreter_intent({
            "raw_question": "2024년에서 2025년 매출 증가율은?",
            "normalized_question": "2024년에서 2025년 매출 증가율은?",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "percentage_change"},
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

    def test_sector_comparison_requires_every_interpreter_sector_member(self):
        intent = adapt_interpreter_intent({
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
        intent = adapt_interpreter_intent({
            "raw_question": "설비투자 매출 비중은?",
            "normalized_question": "설비투자 매출 비중은?",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "ratio_percent"},
            "metric": "capex",
            "basis": "연결",
        })
        facts = extract_facts(adapt_retriever_bundle([
            {"id": "capex", "text": "설비투자 10억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "revenue", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]).documents, intent)
        facts = normalize_facts(facts, intent)[0]
        result = calculate_facts(facts, intent, operation="ratio_percent")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 10.0)

    def test_arithmetic_operations_are_whitelisted(self):
        intent = adapt_interpreter_intent({
            "raw_question": "두 금액의 합계는?",
            "normalized_question": "두 금액의 합계는?",
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "add"},
            "metric": "revenue",
            "basis": "연결",
        })
        facts = extract_facts(adapt_retriever_bundle([
            {"id": "left", "text": "2025년 연결 매출액 10억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "right", "text": "2025년 연결 매출액 5억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
        ]).documents, intent)
        result = calculate_facts(normalize_facts(facts, intent)[0], intent, operation="add")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["result"], 1_500_000_000.0)

    def test_currency_mismatch_is_rejected(self):
        def fact(document_id: str, currency: str) -> ReasonerFact:
            return ReasonerFact(
                metric="revenue", label="매출액", value=100.0, raw_value="100", unit="원",
                normalized_value=100.0, period="2025-12", basis="연결", company=document_id,
                document_id=document_id, source="", evidence="매출액", currency=currency,
            )
        intent = adapt_interpreter_intent({"route": "ok", "intent": "compare", "metric": "revenue", "basis": "연결"})
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
        margin_intent = adapt_interpreter_intent({
            "raw_question": "영업이익률은?", "normalized_question": "영업이익률은?", "route": "ok",
            "intent": "calc", "metric": "operating_profit", "basis": "연결",
            "question_type": "calculation", "calculation": {"operation": "margin"},
        })
        margin_facts = extract_facts(adapt_retriever_bundle([
            {"id": "op", "text": "영업이익 20억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "sales", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]).documents, margin_intent)
        margin_result = calculate_facts(normalize_facts(margin_facts, margin_intent)[0], margin_intent)
        self.assertEqual(margin_result["result"], 20.0)

        cagr_intent = adapt_interpreter_intent({
            "raw_question": "2023년부터 2025년까지 CAGR은?", "normalized_question": "2023년부터 2025년까지 CAGR은?", "route": "ok",
            "intent": "calc", "metric": "revenue", "basis": "연결",
            "question_type": "calculation", "calculation": {"operation": "cagr"},
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
