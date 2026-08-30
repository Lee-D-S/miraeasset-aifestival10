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
    args = parser.parse_args()

    embedder = ClovaQueryEmbedding()

    def throttled_embed(text: str) -> list[float]:
        for attempt in range(args.max_retries + 1):
            try:
                vector = embedder(text)
                if args.request_delay:
                    time.sleep(args.request_delay)
                return vector
            except EmbeddingUnavailable:
                if attempt >= args.max_retries:
                    raise
                time.sleep(min(30.0, 2.0 ** attempt))
        raise AssertionError("unreachable")

    rows = build_chunk_rows(
        args.selection,
        source_root=args.source_root,
        embedder=throttled_embed,
        max_chars=1200,
        max_chunks_per_document=args.max_chunks_per_document,
    )
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
