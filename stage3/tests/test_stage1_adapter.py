from __future__ import annotations

import json
from pathlib import Path
import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.metric_registry import STAGE1_METRICS


class Stage1AdapterTests(unittest.TestCase):
    def test_accepts_actual_stage1_output_fixtures(self):
        fixture_path = Path(__file__).parent / "fixtures" / "stage1_intents.json"
        fixtures = json.loads(fixture_path.read_text(encoding="utf-8"))

        adapted = {name: adapt_stage1_intent(payload) for name, payload in fixtures.items()}

        self.assertEqual(adapted["lookup"].metric_confidence, "high")
        self.assertEqual(adapted["sector_compare"].sector_members, ["LG에너지솔루션", "삼성SDI", "에코프로비엠"])
        self.assertEqual(adapted["contract_list"].manifest_filter["doc_group"], "exchange")
        self.assertEqual(adapted["clarify"].clarify_message, "'삼성'만으로는 기업을 특정할 수 없습니다.")
        self.assertEqual(adapted["unsafe"].reject_reason, "unsafe:investment_advice")
        self.assertEqual(adapted["lookup"].source, fixtures["lookup"])
        self.assertTrue({"revenue", "capex", "supply_contract"}.issubset(STAGE1_METRICS))

    def test_registry_contains_every_stage1_metric(self):
        expected = {
            "revenue", "operating_profit", "net_income", "total_assets", "capex",
            "supply_contract", "contract_termination", "facility_investment",
            "mgmt_judgement", "fundraising", "treasury_stock", "restructuring",
            "major_shareholding", "business_overview", "investment_plan", "rnd",
            "dividend", "employees", "shareholders", "litigation",
        }
        self.assertEqual(set(STAGE1_METRICS), expected)

    def test_preserves_full_stage1_lookup_contract(self):
        raw_intent = {
            "raw_question": "삼성전자의 2025년 연결기준 매출액은?",
            "normalized_question": "삼성전자의 2025년 연결기준 매출액은?",
            "intent": "lookup",
            "route": "ok",
            "corps": [{"corp_name": "삼성전자", "corp_code": "00126380"}],
            "sector": None,
            "sector_members": [],
            "ambiguous_mentions": [],
            "unknown_entities": [],
            "metric": "revenue",
            "metric_confidence": 0.99,
            "basis": "연결",
            "time": {"mode": "fiscal", "years": [2025], "base_months": [12]},
            "correction_mode": "latest_only",
            "allow_pdf_html": True,
            "manifest_filter": {
                "corp_names": ["삼성전자"],
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_years": [2025],
            },
            "doc_count": 1,
            "availability": "available",
            "assumptions": ["연간 보고서"],
            "warnings": [],
            "missing_slots": [],
            "reject_reason": None,
            "clarify_message": None,
            "llm_used": False,
        }

        intent = adapt_stage1_intent(raw_intent)

        self.assertEqual(intent.metric_confidence, 0.99)
        self.assertTrue(intent.allow_pdf_html)
        self.assertEqual(intent.doc_count, 1)
        self.assertEqual(intent.availability, "available")
        self.assertEqual(intent.to_dict()["manifest_filter"]["doc_subtype"], "annual")
        self.assertEqual(intent.source, raw_intent)

    def test_preserves_sector_members_and_clarification_metadata(self):
        intent = adapt_stage1_intent(
            {
                "raw_question": "2차전지 기업 중 설비투자가 가장 큰 곳은?",
                "normalized_question": "2차전지 기업 중 설비투자가 가장 큰 곳은?",
                "route": "ok",
                "intent": "compare",
                "sector": "2차전지",
                "sector_members": ["LG에너지솔루션", "삼성SDI", "에코프로비엠"],
                "metric": "capex",
                "metric_confidence": 0.95,
                "corps": [],
                "ambiguous_mentions": [{"mention": "기업"}],
                "unknown_entities": ["알 수 없는 대상"],
                "missing_slots": [],
                "clarify_message": None,
            }
        )
        self.assertEqual(intent.sector_members, ["LG에너지솔루션", "삼성SDI", "에코프로비엠"])
        self.assertEqual(intent.ambiguous_mentions, [{"mention": "기업"}])
        self.assertEqual(intent.unknown_entities, ["알 수 없는 대상"])

    def test_preserves_excluded_corps_and_think_trace(self):
        intent = adapt_stage1_intent(
            {
                "raw_question": "삼성전자를 제외한 기업의 매출은?",
                "normalized_question": "삼성전자를 제외한 기업의 매출은?",
                "route": "ok",
                "intent": "lookup",
                "excluded_corps": [{"corp_name": "삼성전자", "corp_code": "00126380"}],
                "think_trace": "intent=lookup | 제외=삼성전자",
            }
        )
        self.assertEqual(intent.excluded_corps[0]["corp_name"], "삼성전자")
        self.assertEqual(intent.think_trace, "intent=lookup | 제외=삼성전자")

    def test_preserves_stage1_fields_without_reclassification(self):
        intent = adapt_stage1_intent(
            {
                "raw_question": "삼성전자의 2025년 연결기준 매출액은?",
                "normalized_question": "삼성전자의 2025년 연결기준 매출액은?",
                "route": "ok",
                "intent": "lookup",
                "metric": "revenue",
                "basis": "연결",
                "time": {"mode": "fiscal", "years": [2025], "base_months": [12]},
                "correction_mode": "latest_only",
                "manifest_filter": {"corp_names": ["삼성전자"], "doc_group": "periodic"},
                "corps": [{"corp_name": "삼성전자"}],
                "assumptions": ["연간 보고서"],
                "warnings": [],
            }
        )
        self.assertTrue(intent.is_processable)
        self.assertEqual(intent.intent, "lookup")
        self.assertEqual(intent.metric, "revenue")
        self.assertEqual(intent.basis, "연결")
        self.assertEqual(intent.companies, ["삼성전자"])
        self.assertEqual(intent.manifest_filter["doc_group"], "periodic")

    def test_non_ok_routes_are_not_processable(self):
        for route in ("need_clarify", "unanswerable", "unsafe"):
            intent = adapt_stage1_intent({"question": "질문", "route": route, "intent": "lookup"})
            self.assertFalse(intent.is_processable)
            self.assertEqual(intent.route, route)

    def test_unknown_route_fails_closed(self):
        intent = adapt_stage1_intent({"question": "질문", "route": "unexpected", "intent": "lookup"})
        self.assertEqual(intent.route, "unanswerable")
        self.assertFalse(intent.is_processable)

    def test_question_override_is_explicit(self):
        intent = adapt_stage1_intent({"raw_question": "원 질문", "normalized_question": "정규화 질문", "route": "ok", "intent": "calc"}, question="외부 입력")
        self.assertEqual(intent.question, "외부 입력")
        self.assertEqual(intent.normalized_question, "정규화 질문")


if __name__ == "__main__":
    unittest.main()
