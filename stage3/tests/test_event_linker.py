from __future__ import annotations

import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.event_linker import link_events


class EventLinkerTests(unittest.TestCase):
    def test_links_contract_origin_and_termination_with_evidence_ids(self):
        intent = adapt_stage1_intent({"question": "계약 해지 여부", "route": "ok", "intent": "exists"})
        bundle = adapt_stage2_bundle([
            {"id": "origin", "source": "origin.xml", "text": "단일판매 공급계약 체결 계약명 A", "metadata": {"corp_name": "기업A", "rcept_no": "origin-rcept", "report_nm": "단일판매공급계약체결"}},
            {"id": "termination", "source": "termination.xml", "text": "단일판매 공급계약 해지 계약명 A", "original_rcept_no": "origin-rcept", "metadata": {"corp_name": "기업A", "report_nm": "단일판매공급계약해지"}},
        ])
        links = link_events(bundle.documents, intent)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["relation"], "termination")
        self.assertEqual(links[0]["source_ids"], ["origin", "termination"])

    def test_contract_name_prefix_without_exact_identifier_is_not_linked(self):
        intent = adapt_stage1_intent({"question": "계약 해지 여부", "route": "ok", "intent": "exists"})
        bundle = adapt_stage2_bundle([
            {"id": "origin", "text": "계약명 ABC 계약상대방 기업X 계약금액(원) 100", "metadata": {"corp_name": "기업A", "report_nm": "계약체결"}},
            {"id": "termination", "text": "계약명 ABCD 계약상대방 기업X 계약금액(원) 100", "metadata": {"corp_name": "기업A", "report_nm": "계약해지"}},
        ])
        self.assertEqual(link_events(bundle.documents, intent), [])

    def test_correction_modes_and_missing_reason_are_explicit(self):
        bundle = adapt_stage2_bundle([
            {"id": "origin", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "doc_group": "periodic", "doc_subtype": "annual", "report_nm": "사업보고서", "base_year": 2025, "base_month": 12, "rcept_no": "001", "rcept_dt": "20260301", "is_correction": False}},
            {"id": "corr-old", "text": "[기재정정] 매출액 110억원", "metadata": {"corp_name": "기업A", "doc_group": "periodic", "doc_subtype": "annual", "report_nm": "[기재정정]사업보고서", "base_year": 2025, "base_month": 12, "rcept_no": "002", "rcept_dt": "20260302", "is_correction": True}},
            {"id": "corr-new", "text": "[기재정정] 매출액 120억원", "metadata": {"corp_name": "기업A", "doc_group": "periodic", "doc_subtype": "annual", "report_nm": "[기재정정]사업보고서", "base_year": 2025, "base_month": 12, "rcept_no": "003", "rcept_dt": "20260303", "is_correction": True}},
        ])
        latest = adapt_stage1_intent({"route": "ok", "intent": "change", "correction_mode": "latest_only"})
        latest_links = link_events(bundle.documents, latest)
        self.assertEqual(len(latest_links), 1)
        self.assertEqual(latest_links[0]["followup_document_id"], "corr-new")
        self.assertEqual(latest_links[0]["status"], "insufficient_evidence")

        original = adapt_stage1_intent({"route": "ok", "intent": "change", "correction_mode": "original_only"})
        self.assertEqual(link_events(bundle.documents, original), [])

        chain = adapt_stage1_intent({"route": "ok", "intent": "change", "correction_mode": "include_chain"})
        self.assertEqual({item["followup_document_id"] for item in link_events(bundle.documents, chain)}, {"corr-old", "corr-new"})

    def test_requested_event_field_must_exist_in_origin_and_followup(self):
        intent = adapt_stage1_intent({
            "question": "계약 해지 금액은 얼마인가?",
            "route": "ok",
            "intent": "exists",
            "question_type": "event",
        })
        bundle = adapt_stage2_bundle([
            {"id": "origin", "text": "계약명 A 계약상대방 기업X 계약금액(원) 100", "metadata": {"corp_name": "기업A", "rcept_no": "origin-rcept", "report_nm": "계약체결"}},
            {"id": "termination", "text": "계약명 A 해지", "original_rcept_no": "origin-rcept", "metadata": {"corp_name": "기업A", "report_nm": "계약해지"}},
        ])

        links = link_events(bundle.documents, intent)

        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["status"], "insufficient_evidence")
        self.assertEqual(links[0]["required_fields"], ["amount"])
        self.assertIn("followup.amount", links[0]["missing_fields"])


if __name__ == "__main__":
    unittest.main()
