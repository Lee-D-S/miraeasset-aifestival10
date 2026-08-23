import argparse
import json
from datetime import date
from pathlib import Path

from rag.clients.embedding import EmbeddingClient
from rag.clients.segmentation import SegmentationClient
from rag.config import settings
from rag.ingestion.pipeline import CorpusScanner
from rag.storage.local import LocalVectorRow, LocalVectorStore


def _metadata(document, chunk_index: int, text: str) -> dict[str, str | int]:
    return {
        "corp_name": document.corp_name,
        "corp_code": document.corp_code,
        "document_type": document.document_type,
        "market": document.market,
        "report_period": document.report_period,
        "disclosure_date": document.disclosure_date.isoformat()
        if isinstance(document.disclosure_date, date)
        else "",
        "source_group": document.source_group,
        "source_hash": document.source_hash,
        "chunk_index": chunk_index,
        "chunk_characters": len(text),
    }


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a resumable local vector index with CLOVA segmentation and Embedding v2."
    )
    parser.add_argument("--source", default=settings.source_root, required=not bool(settings.source_root))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-chars", type=int, default=20_000)
    parser.add_argument("--max-chunks", type=int, default=3)
    parser.add_argument("--segmentation-cache", default="vector_db/segmentation_cache.json")
    parser.add_argument("--output", default="vector_db/disclosure_clova_local.json")
    args = parser.parse_args()

    documents, errors = CorpusScanner(args.source).scan(limit=args.limit)
    if errors:
        raise RuntimeError(f"Corpus scan failed: {errors[0]}")
    if not documents:
        raise RuntimeError("No document found")

    segmentation_cache_path = Path(args.segmentation_cache)
    segmentation_cache = _load_json(segmentation_cache_path)
    store = LocalVectorStore.load(args.output)
    segmenter = SegmentationClient()
    embedder = EmbeddingClient()
    indexed_now = 0
    reused_segments = 0
    skipped_embeddings = 0

    for document in documents:
        text = document.text[: args.max_chars]
        cached = segmentation_cache.get(document.document_id, {})
        if (
            cached.get("source_hash") == document.source_hash
            and cached.get("input_characters") == len(text)
            and cached.get("paragraphs")
        ):
            paragraphs = cached["paragraphs"]
            reused_segments += 1
        else:
            result = segmenter.segment_text(text)
            paragraphs = result.paragraphs
            segmentation_cache[document.document_id] = {
                "source_hash": document.source_hash,
                "input_characters": len(text),
                "input_tokens": result.input_tokens,
                "paragraphs": paragraphs,
            }
            _save_json(segmentation_cache_path, segmentation_cache)

        selected = paragraphs if args.max_chunks < 0 else paragraphs[: args.max_chunks]
        for chunk_index, paragraph in enumerate(selected):
            chunk_id = f"{document.document_id}#chunk-{chunk_index}"
            existing = store.rows.get(chunk_id)
            if existing and existing.text == paragraph:
                skipped_embeddings += 1
                continue
            embedding = embedder.embed_text(paragraph)
            store.upsert(
                [
                    LocalVectorRow(
                        id=chunk_id,
                        text=paragraph,
                        source_path=str(document.source_path),
                        embedding=embedding.vector,
                        metadata=_metadata(document, chunk_index, paragraph),
                    )
                ]
            )
            store.save(args.output)
            indexed_now += 1

    store.save(args.output)
    print(
        json.dumps(
            {
                "documents_seen": len(documents),
                "chunks_in_store": len(store),
                "chunks_indexed_now": indexed_now,
                "embeddings_skipped": skipped_embeddings,
                "segmentation_cache_reused": reused_segments,
                "output": args.output,
                "segmentation_cache": str(segmentation_cache_path),
                "scan_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
