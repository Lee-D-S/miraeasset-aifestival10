from __future__ import annotations

import unittest

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.event_linker import link_events


class EventLinkerTests(unittest.TestCase):
    def test_links_contract_origin_and_termination_with_evidence_ids(self):
        intent = adapt_stage1_intent({"question": "계약 해지 여부", "route": "ok", "intent": "exists"})
        bundle = adapt_stage2_bundle([
            {"id": "origin", "source": "origin.xml", "text": "단일판매 공급계약 체결 계약명 A", "metadata": {"corp_name": "기업A", "report_nm": "단일판매공급계약체결"}},
            {"id": "termination", "source": "termination.xml", "text": "단일판매 공급계약 해지 계약명 A", "metadata": {"corp_name": "기업A", "report_nm": "단일판매공급계약해지"}},
        ])
        links = link_events(bundle.documents, intent)
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0]["relation"], "termination")
        self.assertEqual(links[0]["source_ids"], ["origin", "termination"])


if __name__ == "__main__":
    unittest.main()
