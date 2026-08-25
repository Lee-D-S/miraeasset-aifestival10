from __future__ import annotations

import unittest

from stage3.adapters.stage1 import adapt_stage1_intent


class Stage1AdapterTests(unittest.TestCase):
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
