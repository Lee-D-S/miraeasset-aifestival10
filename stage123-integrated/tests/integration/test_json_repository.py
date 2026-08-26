from __future__ import annotations

import json
import unittest
from pathlib import Path

from integration.json_repository import JsonStage2Repository
from integration.stage2_repository import RetrievalError, SearchRequest


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT.parent / "test_data" / "disclosure_clova_local.json"


class JsonRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = json.loads(DATA.read_text(encoding="utf-8"))
        self.target = next(row for row in self.rows if "매출액" in row["text"])
        self.request = SearchRequest(
            query="매출액",
            corp_names=(self.rows[0]["metadata"]["corp_name"],),
            doc_group="periodic",
            doc_subtype="quarter",
            base_years=(2023,),
            base_months=(3,),
            top_k=2,
        )

    def test_semantic_search_preserves_structured_document_contract(self) -> None:
        repository = JsonStage2Repository.from_path(
            DATA,
            query_embedder=lambda _query: self.target["embedding"],
        )
        result = repository.search(self.request)
        self.assertEqual(result.status, "ok")
        self.assertEqual(len(result.documents), 2)
        document = result.documents[0]
        self.assertTrue(document["id"])
        self.assertTrue(document["text"])
        self.assertIn("metadata", document)
        self.assertEqual(document["metadata"]["chunk_id"], document["id"])
        self.assertEqual(document["metadata"]["base_year"], 2023)
        self.assertTrue(any(item.startswith("candidate_count=") for item in result.retrieval_trace))

    def test_missing_query_embedding_is_explicit_failure(self) -> None:
        repository = JsonStage2Repository.from_path(DATA)
        with self.assertRaises(RetrievalError) as raised:
            repository.search(self.request)
        self.assertEqual(raised.exception.classification, "embedding_unavailable")

    def test_keyword_search_is_marked_as_smoke_only(self) -> None:
        repository = JsonStage2Repository.from_path(DATA)
        result = repository.keyword_search(self.request)
        self.assertIn("mode=keyword_smoke", result.retrieval_trace)
        self.assertTrue(any("keyword_smoke" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
