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

    def test_normalization_applies_stage1_period_and_basis(self):
        bundle = adapt_stage2_bundle([{"id": "d1", "text": "매출액 100억원", "metadata": {"corp_name": "기업A"}}])
        facts, warnings = normalize_facts(extract_facts(bundle.documents, self.intent), self.intent)
        numeric = next(item for item in facts if item.metric == "revenue")
        self.assertEqual(numeric.period, "2025-12")
        self.assertEqual(numeric.basis, "연결")
        self.assertFalse(warnings)

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


if __name__ == "__main__":
    unittest.main()
