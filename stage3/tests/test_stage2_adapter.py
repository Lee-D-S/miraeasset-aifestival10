from __future__ import annotations

import unittest

from stage3.adapters.stage2 import adapt_stage2_bundle


class Stage2AdapterTests(unittest.TestCase):
    def test_normalizes_standard_stage2_bundle_and_preserves_manifest_metadata(self):
        bundle = adapt_stage2_bundle(
            {
                "documents": [
                    {
                        "id": "exchange_20230131800162",
                        "source": "raw/exchange/HD현대일렉트릭/20230131800162/20230131800162.xml",
                        "text": "계약금액(원) 97,000,000,000",
                        "score": 0.91,
                        "metadata": {
                            "corp_name": "HD현대일렉트릭",
                            "corp_code": "00401731",
                            "doc_group": "exchange",
                            "doc_subtype": "단일판매공급계약체결",
                            "report_nm": "단일판매ㆍ공급계약 체결",
                            "rcept_no": "20230131800162",
                            "rcept_dt": "20230131",
                            "base_year": None,
                            "base_month": None,
                            "is_correction": False,
                            "file_path": "raw/exchange/...",
                            "file_format": "xml",
                            "flr_nm": "HD현대일렉트릭",
                            "n_files": 1,
                        },
                        "evidence_spans": [{"text": "계약금액(원) 97,000,000,000", "start": 0, "end": 30, "label": "계약금액"}],
                    }
                ],
                "cited_documents": [],
                "retrieval_trace": ["retrieved=1"],
            }
        )
        document = bundle.documents[0]
        self.assertEqual(document.id, "exchange_20230131800162")
        self.assertEqual(document.metadata["rcept_no"], "20230131800162")
        self.assertEqual(document.metadata["file_format"], "xml")
        self.assertEqual(document.metadata["n_files"], 1)
        self.assertEqual(document.evidence_spans[0]["label"], "계약금액")

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

    def test_top_level_metadata_is_fallback_and_cited_documents_have_priority(self):
        bundle = adapt_stage2_bundle(
            {
                "documents": [{"id": "retrieved", "text": "검색 근거", "corp_name": "기업A", "file_format": "pdf+html"}],
                "citedDocuments": [{"document_id": "cited", "doc": "인용 근거", "source_path": "cited.html", "citations": [{"text": "인용 근거", "start": 0, "end": 5}], "corp_name": "기업B"}],
            }
        )
        self.assertEqual(bundle.documents[0].metadata["file_format"], "pdf+html")
        self.assertEqual(bundle.effective_documents()[0].id, "cited")
        self.assertEqual(bundle.effective_documents()[0].metadata["corp_name"], "기업B")
        self.assertEqual(bundle.effective_documents()[0].evidence_spans[0]["text"], "인용 근거")

    def test_documents_without_ids_are_not_stage3_evidence(self):
        bundle = adapt_stage2_bundle(
            {
                "documents": [
                    {"text": "ID 없는 문서"},
                    {"id": "with-span", "text": "", "evidence_spans": [{"text": "근거 span"}]},
                ]
            }
        )
        self.assertEqual([document.id for document in bundle.documents], ["with-span"])
        self.assertEqual(bundle.documents[0].evidence_spans[0]["text"], "근거 span")


if __name__ == "__main__":
    unittest.main()
