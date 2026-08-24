import json
import tempfile
import unittest
from pathlib import Path

from agentic_rag.ingestion.pipeline import build_local_index
from agentic_rag.infrastructure.embedding_adapter import DeterministicEmbedding
from agentic_rag.infrastructure.retrieval import LocalVectorRetriever


class IngestionTests(unittest.TestCase):
    def test_build_local_index_is_deterministic_and_searchable(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "doc.txt").write_text("기업A 매출 100\n사업 내용", encoding="utf-8")
            output = Path(directory) / "index.json"
            count = build_local_index(str(source), str(output), embedder=DeterministicEmbedding())
            self.assertGreaterEqual(count, 1)
            rows = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(rows[0]["id"], json.loads(output.read_text(encoding="utf-8"))[0]["id"])
            self.assertTrue(LocalVectorRetriever(str(output)).search("기업A", 1, {}))

    def test_failed_documents_are_recorded_and_successful_documents_continue(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "good.txt").write_text("정상 문서", encoding="utf-8")
            (source / "bad.pdf").write_bytes(b"not a pdf")
            output = Path(directory) / "index.json"
            failures = Path(directory) / "failures.json"
            count = build_local_index(str(source), str(output), embedder=DeterministicEmbedding(), failure_log=str(failures))
            self.assertEqual(count, 1)
            self.assertIn("bad.pdf", failures.read_text(encoding="utf-8"))

    def test_rerun_reuses_the_same_chunk_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            (source / "doc.txt").write_text("변경 감지 문서", encoding="utf-8")
            output = Path(directory) / "index.json"
            build_local_index(str(source), str(output), embedder=DeterministicEmbedding())
            first_ids = [row["id"] for row in json.loads(output.read_text(encoding="utf-8"))]
            build_local_index(str(source), str(output), embedder=DeterministicEmbedding())
            second_ids = [row["id"] for row in json.loads(output.read_text(encoding="utf-8"))]
            self.assertEqual(first_ids, second_ids)
