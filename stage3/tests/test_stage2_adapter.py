from __future__ import annotations

import unittest

from stage3.adapters.stage2 import adapt_stage2_bundle


class Stage2AdapterTests(unittest.TestCase):
    def test_normalizes_retrieved_and_cited_documents(self):
        bundle = adapt_stage2_bundle(
            {
                "retrieved_documents": [
                    {
                        "doc_id": "d1",
                        "source_path": "a.xml",
                        "content": "매출액 100억원",
                        "similarity": "0.8",
                        "corp_name": "기업A",
                    }
                ],
                "citedDocuments": [
                    {
                        "id": "d2",
                        "source": "b.xml",
                        "text": "매출액 120억원",
                        "evidence_spans": [{"start": 0, "end": 8}],
                        "metadata": {"corp_name": "기업B"},
                    }
                ],
                "trace": ["retrieved=1", "cited=1"],
            }
        )

        self.assertEqual(bundle.documents[0].id, "d1")
        self.assertEqual(bundle.documents[0].metadata["corp_name"], "기업A")
        self.assertEqual(bundle.documents[0].score, 0.8)
        self.assertEqual(bundle.cited_documents[0].evidence_spans[0]["start"], 0)
        self.assertEqual(bundle.effective_documents()[0].id, "d2")
        self.assertEqual(bundle.retrieval_trace, ["retrieved=1", "cited=1"])

    def test_accepts_plain_document_list_and_missing_optional_fields(self):
        bundle = adapt_stage2_bundle([{"id": "d1", "text": "근거"}])
        self.assertEqual(len(bundle.documents), 1)
        self.assertEqual(bundle.documents[0].source, "")
        self.assertIsNone(bundle.documents[0].score)
        self.assertEqual(bundle.effective_documents()[0].text, "근거")

    def test_preserves_unknown_top_level_metadata_as_raw(self):
        bundle = adapt_stage2_bundle({"documents": [{"id": "d1", "text": "근거", "custom": "kept"}]})
        self.assertEqual(bundle.documents[0].raw["custom"], "kept")


if __name__ == "__main__":
    unittest.main()
