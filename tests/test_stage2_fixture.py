from __future__ import annotations

import json
import unittest
from pathlib import Path

from stage2 import JsonFixtureRetriever
from stage2.retrieval import RetrievalConfig, retrieve


FIXTURE = Path(__file__).parents[1] / "legacy" / "test_data" / "disclosure_clova_local.json"


class Stage2FixtureTests(unittest.TestCase):
    def test_fixture_metadata_is_normalized_and_filterable(self) -> None:
        rows = json.loads(FIXTURE.read_text(encoding="utf-8"))
        retriever = JsonFixtureRetriever(rows)
        metadata = retriever.documents[0]["metadata"]
        self.assertIn(metadata["doc_group"], {"periodic", "non_periodic"})
        self.assertIsNotNone(metadata["doc_subtype"])
        self.assertIsNotNone(metadata["base_year"])
        self.assertEqual(len(retriever.filter_candidates({"corp_names": [metadata["corp_name"]]}, 50)), len(retriever.documents))

    def test_missing_query_embedding_is_explicit_failure(self) -> None:
        retriever = JsonFixtureRetriever.from_path(FIXTURE)
        intent = {"manifest_filter": {}, "normalized_question": "매출액", "metric": "매출액"}
        result = retrieve(
            question_id="fixture-1",
            question="매출액",
            intent=intent,
            route="ok",
            retriever=retriever,
            config=RetrievalConfig(final_limit=3),
        )
        self.assertEqual(result["status"], "embedding_unavailable")
        self.assertEqual(result["cited_documents"], [])

    def test_query_embedding_dimension_is_strict(self) -> None:
        retriever = JsonFixtureRetriever.from_path(FIXTURE, query_embedder=lambda _query: [0.0] * 3)
        with self.assertRaises(ValueError):
            retriever.vector_search("query", retriever.documents[:1], 1)


if __name__ == "__main__":
    unittest.main()
