import hashlib
from dataclasses import dataclass, field

from rag.clients.embedding import EmbeddingClient
from rag.clients.segmentation import SegmentationClient
from rag.ingestion.pipeline import CorpusScanner
from rag.models import ChunkRecord
from rag.storage.postgres import PostgresStore


@dataclass
class IndexingReport:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)


class IndexingService:
    def __init__(
        self,
        scanner: CorpusScanner,
        store: PostgresStore,
        segmenter: SegmentationClient | None = None,
        embedder: EmbeddingClient | None = None,
    ) -> None:
        self.scanner = scanner
        self.store = store
        self.segmenter = segmenter or SegmentationClient()
        self.embedder = embedder or EmbeddingClient()

    def run(self, *, limit: int | None = None) -> IndexingReport:
        records, scan_errors = self.scanner.scan(limit=limit)
        report = IndexingReport(scanned=len(records), errors=list(scan_errors))
        for document in records:
            try:
                if self.store.document_is_current(document.document_id, document.source_hash):
                    report.skipped += 1
                    continue

                segmentation = self.segmenter.segment_text(document.text)
                chunks = []
                for index, text in enumerate(segmentation.paragraphs):
                    embedding = self.embedder.embed_text(text).vector
                    chunks.append(ChunkRecord(
                        chunk_id=f"{document.document_id}:{index}",
                        document_id=document.document_id,
                        chunk_index=index,
                        text=text,
                        source_path=str(document.source_path),
                        embedding=embedding,
                        span=segmentation.spans[index] if index < len(segmentation.spans) else [],
                        text_hash=chunk_text_hash(text),
                    ))
                if not chunks:
                    report.errors.append(f"{document.document_id}: no chunks")
                    continue

                self.store.upsert_document(document)
                self.store.delete_chunks(document.document_id)
                report.chunks += self.store.upsert_chunks(chunks)
                report.indexed += 1
            except Exception as error:  # noqa: BLE001 - continue indexing other documents
                report.errors.append(f"{document.document_id}: {type(error).__name__}: {error}")
        return report


def chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
