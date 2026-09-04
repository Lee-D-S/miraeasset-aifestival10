from __future__ import annotations

from pathlib import Path
import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.fact_extraction import extract_facts
from stage3.deterministic.normalization import normalize_facts


class FactExtractionTests(unittest.TestCase):
    def setUp(self):
        self.intent = adapt_stage1_intent({
            "raw_question": "기업A의 2025년 연결 매출액은?",
            "normalized_question": "기업A의 2025년 연결 매출액은?",
            "route": "ok",
            "intent": "lookup",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        })

    def test_extracts_numeric_date_unit_and_evidence(self):
        bundle = adapt_stage2_bundle([{
            "id": "d1",
            "source": "report.xml",
            "text": "2025년 연결 기준 매출액은 1,000억원이며 계약일은 2025년 3월 2일이다.",
            "metadata": {"corp_name": "기업A", "report_period": "2025-12"},
        }])
        facts = extract_facts(bundle.documents, self.intent)
        numeric = next(item for item in facts if item.metric == "revenue")
        date = next(item for item in facts if item.kind == "date" and "3월" in str(item.value))
        self.assertEqual(numeric.value, 1000.0)
        self.assertEqual(numeric.unit, "억원")
        self.assertEqual(numeric.normalized_value, 100000000000.0)
        self.assertEqual(numeric.period, "2025-12")
        self.assertEqual(numeric.basis, "연결")
        self.assertIn("매출액", numeric.evidence)
        self.assertEqual(date.value, "2025년 3월 2일")

    def test_normalizes_compound_korean_amount_and_preserves_display(self):
        bundle = adapt_stage2_bundle([{
            "id": "compound",
            "source": "report.xml",
            "text": "2024년 연결 매출액은 300조 8,709억원이다.",
            "metadata": {"corp_name": "기업A", "report_period": "2024-12"},
        }])

        fact = next(item for item in extract_facts(bundle.documents, self.intent) if item.metric == "revenue")

        self.assertEqual(fact.value, 300_870_900_000_000.0)
        self.assertEqual(fact.normalized_value, 300_870_900_000_000.0)
        self.assertEqual(fact.raw_value, "300조 8,709억원")
        self.assertEqual(fact.display_value, "300조 8,709억원")
        self.assertEqual(fact.unit, "원")
        self.assertEqual(fact.currency, "KRW")

    def test_classifies_total_and_segment_scopes_from_evidence(self):
        bundle = adapt_stage2_bundle([{
            "id": "scoped",
            "source": "report.xml",
            "text": (
                "2025년 연결 매출액 300조원. "
                "부문별 매출현황 자동차 매출액 200조원."
            ),
            "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
        }])

        facts = [item for item in extract_facts(bundle.documents, self.intent) if item.metric == "revenue"]

        self.assertEqual({item.aggregation_scope for item in facts}, {"total", "segment"})

    def test_total_revenue_ignores_prior_revenue_type_table_cell(self):
        intent = adapt_stage1_intent({
            "raw_question": "삼성전자 2024년 연결 매출액은 얼마인가요?",
            "normalized_question": "삼성전자 2024년 연결 매출액은 얼마인가요?",
            "route": "ok",
            "intent": "lookup",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2024], "base_months": [12]},
        })
        bundle = adapt_stage2_bundle([
            {
                "id": "20250311001085_121",
                "source": "samsung.xml",
                "text": "2024년 당사의 매출은 300조 8,709억원으로 전년 대비 증가하였다.",
                "metadata": {"corp_name": "삼성전자", "base_year": 2024, "base_month": 12},
            },
            {
                "id": "20250311001085_172",
                "source": "samsung.xml",
                "text": (
                    "| 구분 | 제56기 | 제55기 | 제54기 |\n"
                    "| --- | --- | --- | --- |\n"
                    "| 용역 및 기타매출 | 75,092 | 127,975 | 118,853 |\n"
                    "| 계 | 3,008,709 | 2,589,355 | 3,022,314 |"
                ),
                "metadata": {"corp_name": "삼성전자", "base_year": 2024, "base_month": 12},
            },
        ])

        facts = [item for item in extract_facts(bundle.documents, intent) if item.metric == "revenue"]
        prior_type = next(item for item in facts if item.value == 127975.0)
        self.assertEqual(prior_type.period, "2023")
        self.assertEqual(prior_type.aggregation_scope, "unknown")

        from stage3.agents.answer import _lookup_facts
        from stage3.grounding import matching_facts

        matched = matching_facts(facts, intent)
        self.assertEqual([(item.document_id, item.display_value) for item in matched], [
            ("20250311001085_121", "300조 8,709억원"),
        ])
        selected = _lookup_facts(intent, facts)
        self.assertEqual(selected[0].document_id, "20250311001085_121")

    def test_normalization_applies_stage1_period_and_basis(self):
        bundle = adapt_stage2_bundle([{"id": "d1", "text": "매출액 100억원", "metadata": {"corp_name": "기업A"}}])
        facts, warnings = normalize_facts(extract_facts(bundle.documents, self.intent), self.intent)
        numeric = next(item for item in facts if item.metric == "revenue")
        self.assertEqual(numeric.period, "2025-12")
        self.assertEqual(numeric.basis, "연결")
        self.assertFalse(warnings)

    def test_does_not_classify_revenue_receivables_as_revenue(self):
        bundle = adapt_stage2_bundle([{
            "id": "financial-position",
            "text": "매출채권 76,710,079 매출액 333,605,938",
            "metadata": {"corp_name": "삼성전자", "report_period": "2025-12"},
        }])

        facts = extract_facts(bundle.documents, self.intent)

        self.assertEqual([item.value for item in facts if item.metric == "revenue"], [333605938.0])

    def test_unknown_unit_is_warned(self):
        bundle = adapt_stage2_bundle([{"id": "d1", "text": "매출액 100단위", "metadata": {"corp_name": "기업A"}}])
        facts = extract_facts(bundle.documents, self.intent)
        normalized, warnings = normalize_facts(facts, self.intent)
        self.assertTrue(normalized)
        self.assertTrue(any("단위" in warning for warning in warnings))

    def test_extracts_table_context_unit_and_delta_from_structured_evidence(self):
        fixture = Path(__file__).parent / "fixtures" / "samsung_dart.xml"
        bundle = adapt_stage2_bundle([{
            "id": "samsung-xml",
            "source": "samsung.xml",
            "text": fixture.read_text(encoding="utf-8"),
            "metadata": {"corp_name": "삼성전자", "report_period": "2025-12"},
        }])
        facts = extract_facts(bundle.documents, self.intent)
        revenue_facts = [item for item in facts if item.metric == "revenue"]
        self.assertEqual({item.value for item in revenue_facts}, {1000.0, -120.0})
        self.assertTrue(all(item.unit == "억원" for item in revenue_facts))
        self.assertTrue(any(item.table_context.get("row_label") == "연결조정 후" for item in revenue_facts))
        self.assertTrue(all(item.currency == "KRW" for item in revenue_facts))

    def test_extracts_spaced_korean_labels_from_local_markdown_chunk(self):
        bundle = adapt_stage2_bundle([{
            "id": "samsung-markdown",
            "source": "samsung.md",
            "text": (
                "[삼성전자 | 사업보고서 (2023.12)]\n"
                "| 과목 | 주석 | 제 55 (당) 기 | 제 54 (전) 기 |\n"
                "| --- | --- | --- | --- |\n"
                "| Ⅰ. 매    출    액 | 29 |  | 258,935,494 |  | 302,231,360 |"
            ),
            "metadata": {"corp_name": "삼성전자", "base_year": 2023, "base_month": 12, "basis": "연결"},
        }])
        facts = [item for item in extract_facts(bundle.documents, self.intent) if item.metric == "revenue"]
        current = next(item for item in facts if item.value == 258935494.0)
        prior = next(item for item in facts if item.value == 302231360.0)
        self.assertEqual(current.period, "2023")
        self.assertEqual(prior.period, "2022")
        self.assertEqual(current.unit, "백만원")
        self.assertEqual(prior.unit, "백만원")
        self.assertNotIn(29.0, [item.value for item in facts])

    def test_stage1_total_assets_splits_balance_sheet_fact_metrics(self):
        intent = adapt_stage1_intent({
            "route": "ok",
            "intent": "lookup",
            "metric": "total_assets",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        })
        bundle = adapt_stage2_bundle([{
            "id": "balance-sheet",
            "text": "2025년 연결 자산총계 100억원 부채총계 40억원 자본총계 60억원",
            "metadata": {"corp_name": "기업A", "report_period": "2025-12"},
        }])
        facts = extract_facts(bundle.documents, intent)
        self.assertEqual({item.metric for item in facts if item.kind == "numeric"}, {"assets", "liabilities", "equity"})

    def test_extracts_supply_contract_fields_and_amount(self):
        intent = adapt_stage1_intent({"route": "ok", "intent": "list", "metric": "supply_contract"})
        bundle = adapt_stage2_bundle([{
            "id": "contract-1",
            "text": "체결계약명 2500kVA 배전변압기 등 3,500대 계약금액(원) 97,000,000,000 계약상대방 Hyundai Electric America Corporation 판매·공급지역 미국",
            "metadata": {"corp_name": "HD현대일렉트릭", "report_period": "2023-01"},
        }])
        facts = extract_facts(bundle.documents, intent)
        contract_name = next(item for item in facts if item.label == "contract_name")
        amount = next(item for item in facts if item.kind == "numeric" and item.label == "계약금액(원)")
        counterparty = next(item for item in facts if item.label == "counterparty")
        self.assertTrue(str(contract_name.value).startswith("2500kVA"))
        self.assertEqual(amount.value, 97000000000.0)
        self.assertEqual(amount.unit, "원")
        self.assertEqual(counterparty.value, "Hyundai Electric America Corporation")

    def test_extracts_text_section_fact_for_rnd(self):
        intent = adapt_stage1_intent({"route": "ok", "intent": "lookup", "metric": "rnd"})
        bundle = adapt_stage2_bundle([{
            "id": "rnd-1",
            "text": "연구개발 당사는 차세대 제품을 개발하고 있다.",
            "metadata": {"corp_name": "기업A", "report_period": "2025"},
        }])
        facts = extract_facts(bundle.documents, intent)
        text_fact = next(item for item in facts if item.kind == "text")
        self.assertEqual(text_fact.metric, "rnd")
        self.assertIn("연구개발", text_fact.value)

    def test_extracts_debt_and_equity_ratios_from_markdown_cells(self):
        intent = adapt_stage1_intent({
            "raw_question": "삼성전자의 2025년 부채비율은?",
            "normalized_question": "삼성전자의 2025년 부채비율은?",
            "route": "ok",
            "intent": "lookup",
            "metric": "total_assets",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        })
        bundle = adapt_stage2_bundle([{
            "id": "ratio",
            "text": (
                "| 구분 | 제 57 (당) 기 |\n"
                "| 부채비율 | 41.1% |\n"
                "| 자기자본비율 | 70.8% |"
            ),
            "metadata": {"corp_name": "삼성전자", "base_year": 2025, "base_month": 12, "basis": "연결"},
        }])
        facts = extract_facts(bundle.documents, intent)
        debt = next(item for item in facts if item.label == "부채비율")
        equity = next(item for item in facts if item.label == "자기자본비율")
        self.assertEqual(debt.metric, "ratio")
        self.assertEqual(debt.unit, "%")
        self.assertEqual(debt.value, 41.1)
        self.assertEqual(equity.metric, "ratio")
        self.assertEqual(equity.value, 70.8)
        from stage3.grounding import matching_facts
        self.assertTrue(matching_facts([debt], intent))
        equity_intent = adapt_stage1_intent({
            "raw_question": "삼성전자의 2025년 자기자본비율은?",
            "normalized_question": "삼성전자의 2025년 자기자본비율은?",
            "route": "ok",
            "intent": "lookup",
            "metric": "total_assets",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        })
        self.assertTrue(matching_facts([equity], equity_intent))

    def test_rejects_glued_table_numbers(self):
        intent = adapt_stage1_intent({
            "route": "ok",
            "intent": "lookup",
            "metric": "operating_profit",
            "basis": "연결",
            "time": {"years": [2026], "base_months": [3]},
        })
        bundle = adapt_stage2_bundle([{
            "id": "glued",
            "text": "영업이익1,852,115579,183148,2952,579,593",
            "metadata": {"corp_name": "현대자동차", "report_period": "2026-03", "basis": "연결"},
        }])
        facts = extract_facts(bundle.documents, intent)
        numeric = [item for item in facts if item.kind == "numeric"]
        self.assertTrue(numeric)
        self.assertFalse([item for item in numeric if abs(float(item.value)) > 1e15])
        self.assertEqual(numeric[0].value, 1_852_115.0)


if __name__ == "__main__":
    unittest.main()
