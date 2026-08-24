"""Index builders owned by the LangGraph RAG backend."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from langgraph_rag.ingestion import CorpusScanner
from langgraph_rag.runtime import EmbeddingClient, LocalVectorRow, LocalVectorStore, PostgresStore, SegmentationClient


def document_metadata(document, chunk_index: int, text: str) -> dict[str, str | int]:
    return {
        "corp_name": document.corp_name,
        "corp_code": document.corp_code,
        "document_type": document.document_type,
        "market": document.market,
        "report_period": document.report_period,
        "disclosure_date": document.disclosure_date.isoformat() if isinstance(document.disclosure_date, date) else "",
        "source_group": document.source_group,
        "source_hash": document.source_hash,
        "chunk_index": chunk_index,
        "chunk_characters": len(text),
    }


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def build_local_index(source: str, *, limit: int = 1, max_chars: int = 20_000, chunk_start: int = 0,
                      max_chunks: int = 3, segmentation_cache: str = "test_data/segmentation_cache.json",
                      output: str = "test_data/disclosure_clova_local.json") -> dict:
    documents, errors = CorpusScanner(source).scan(limit=limit)
    if not documents:
        raise RuntimeError(f"No document found. Errors: {errors[:1]}")
    cache_path = Path(segmentation_cache)
    cache = _load_json(cache_path)
    store = LocalVectorStore.load(output)
    segmenter, embedder = SegmentationClient(), EmbeddingClient()
    indexed_now = 0
    skipped_embeddings = 0
    for document in documents:
        text = document.text[:max_chars]
        cached = cache.get(document.document_id, {})
        if cached.get("source_hash") == document.source_hash and cached.get("input_characters") == len(text) and cached.get("paragraphs"):
            paragraphs = cached["paragraphs"]
        else:
            result = segmenter.segment_text(text)
            paragraphs = result.paragraphs
            cache[document.document_id] = {"source_hash": document.source_hash, "input_characters": len(text), "input_tokens": result.input_tokens, "paragraphs": paragraphs}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        available = list(enumerate(paragraphs[chunk_start:], start=chunk_start))
        selected = available if max_chunks < 0 else available[:max_chunks]
        for chunk_index, paragraph in selected:
            chunk_id = f"{document.document_id}#chunk-{chunk_index}"
            existing = store.rows.get(chunk_id)
            if existing and existing.text == paragraph:
                skipped_embeddings += 1
                continue
            store.upsert([LocalVectorRow(chunk_id, paragraph, str(document.source_path), embedder.embed_text(paragraph).vector, document_metadata(document, chunk_index, paragraph))])
            store.save(output)
            indexed_now += 1
    store.save(output)
    return {"documents_seen": len(documents), "chunks_in_store": len(store), "chunks_indexed_now": indexed_now, "embeddings_skipped": skipped_embeddings, "output": output, "segmentation_cache": segmentation_cache, "scan_errors": errors}


@dataclass
class PostgresIndexReport:
    scanned: int = 0
    indexed: int = 0
    skipped: int = 0
    chunks: int = 0
    errors: list[str] = field(default_factory=list)


def build_postgres_index(source: str, dsn: str, *, limit: int | None = None) -> PostgresIndexReport:
    scanner, store = CorpusScanner(source), PostgresStore(dsn)
    store.initialize()
    segmenter, embedder = SegmentationClient(), EmbeddingClient()
    records, scan_errors = scanner.scan(limit=limit)
    report = PostgresIndexReport(scanned=len(records), errors=list(scan_errors))
    for document in records:
        try:
            if store.document_is_current(document.document_id, document.source_hash):
                report.skipped += 1
                continue
            segmentation = segmenter.segment_text(document.text)
            chunks = []
            for index, text in enumerate(segmentation.paragraphs):
                chunks.append(type("Chunk", (), {
                    "chunk_id": f"{document.document_id}:{index}", "document_id": document.document_id,
                    "chunk_index": index, "text": text, "source_path": str(document.source_path),
                    "embedding": embedder.embed_text(text).vector,
                    "span": segmentation.spans[index] if index < len(segmentation.spans) else [],
                    "text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                })())
            if not chunks:
                report.errors.append(f"{document.document_id}: no chunks")
                continue
            store.upsert_document(document)
            store.delete_chunks(document.document_id)
            report.chunks += store.upsert_chunks(chunks)
            report.indexed += 1
        except Exception as error:  # noqa: BLE001
            report.errors.append(f"{document.document_id}: {type(error).__name__}: {error}")
    return report
