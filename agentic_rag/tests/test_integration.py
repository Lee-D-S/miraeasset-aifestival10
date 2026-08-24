import os
import tempfile
import unittest
from pathlib import Path

from agentic_rag.infrastructure.embedding_adapter import DeterministicEmbedding
from agentic_rag.infrastructure.postgres import PostgresIndexWriter, PostgresVectorRetriever
from agentic_rag.ingestion.pipeline import build_local_index


@unittest.skipUnless(os.getenv("AGENTIC_POSTGRES_TEST_DSN"), "AGENTIC_POSTGRES_TEST_DSN is not configured")
class PostgresIntegrationTests(unittest.TestCase):
    def test_ingest_and_retrieve_same_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "integration.txt").write_text("기업A 통합 테스트 매출 100", encoding="utf-8")
            writer = PostgresIndexWriter(os.environ["AGENTIC_POSTGRES_TEST_DSN"])
            count = build_local_index(str(source), "", embedder=DeterministicEmbedding(), writer=writer)
            self.assertGreater(count, 0)
            retriever = PostgresVectorRetriever(os.environ["AGENTIC_POSTGRES_TEST_DSN"], DeterministicEmbedding())
            self.assertTrue(retriever.healthcheck())
            self.assertTrue(retriever.search("기업A", 1, {}))


if __name__ == "__main__":
    unittest.main()
