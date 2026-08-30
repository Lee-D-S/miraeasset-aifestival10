"""Build the small real-document embedding artifact for local smoke tests."""

from __future__ import annotations

import argparse
import json
import time
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stage2.embedding import ClovaQueryEmbedding
from stage2.json_fixture import EmbeddingUnavailable
from stage2.ingestion import build_chunk_rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, help="Competition data root containing data/3.공시")
    parser.add_argument("--selection", default="data/local_smoke/selected_documents.json")
    parser.add_argument("--output", default="data/local_smoke/embedded_chunks.json")
    parser.add_argument("--max-chunks-per-document", type=int, default=10)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--max-retries", type=int, default=4)
    parser.add_argument("--doc-id", action="append", default=[], help="Only embed these document IDs")
    parser.add_argument("--term", action="append", default=[], help="Only embed chunks containing one of these terms")
    args = parser.parse_args()

    embedder = ClovaQueryEmbedding()

    def throttled_embed(text: str) -> list[float]:
        for attempt in range(args.max_retries + 1):
            try:
                vector = embedder(text)
                if args.request_delay:
                    time.sleep(args.request_delay)
                return vector
            except EmbeddingUnavailable as error:
                if attempt >= args.max_retries or not error.retryable:
                    raise
                wait = error.retry_after if error.retry_after is not None else min(60.0, 2.0 ** attempt)
                time.sleep(max(wait, 1.0))
        raise AssertionError("unreachable")

    rows = build_chunk_rows(
        args.selection,
        source_root=args.source_root,
        embedder=throttled_embed,
        max_chars=1200,
        max_chunks_per_document=None if args.max_chunks_per_document == 0 else args.max_chunks_per_document,
    )
    if args.doc_id:
        rows = [row for row in rows if row["doc_id"] in set(args.doc_id)]
    if args.term:
        rows = [row for row in rows if any(term in row["text"] for term in args.term)]
    if not rows:
        raise ValueError("no chunks matched the requested document/term filters")
    invalid = [row["id"] for row in rows if len(row.get("embedding", [])) != 1024]
    if invalid:
        raise RuntimeError(f"embedding dimension validation failed for {len(invalid)} chunks")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    print(f"embedded_chunks={len(rows)}")
    print(f"documents={len({row['doc_id'] for row in rows})}")
    print("dimension=1024")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
