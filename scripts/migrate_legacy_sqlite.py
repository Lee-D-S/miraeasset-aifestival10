"""Migrate the legacy SQLite embedding artifact to SQLite + Chroma.

The legacy index stores vectors in ``embedding_json``.  The current Stage2
backend stores metadata in SQLite and vectors in Chroma, so this command
rewrites the rows without calling the embedding provider.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stage2.embedding import ClovaEmbeddings
from stage2.local_store import LocalHybridRetriever


def load_rows(source: Path) -> list[dict]:
    connection = sqlite3.connect(source)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(chunks)")}
        required = {"id", "doc_id", "text", "source_path", "metadata_json", "embedding_json"}
        missing = sorted(required - columns)
        if missing:
            raise ValueError("legacy chunks schema missing: " + ", ".join(missing))
        rows = []
        for row in connection.execute(
            "SELECT id, doc_id, text, source_path, metadata_json, embedding_json FROM chunks ORDER BY id"
        ):
            metadata = json.loads(row[4])
            embedding = json.loads(row[5])
            if not isinstance(metadata, dict) or not isinstance(embedding, list):
                raise ValueError(f"invalid legacy row: {row[0]}")
            rows.append({
                "id": str(row[0]),
                "doc_id": str(row[1]),
                "chunk_id": str(row[0]),
                "text": str(row[2]),
                "source_path": str(row[3]),
                "metadata": metadata,
                "embedding": embedding,
            })
        return rows
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/local_smoke/smoke.db"))
    parser.add_argument("--output", type=Path, default=Path("data/local_smoke/refactor_smoke.db"))
    parser.add_argument("--chroma-dir", type=Path, default=Path("data/local_smoke/refactor_smoke_chroma"))
    args = parser.parse_args()

    if args.input.resolve() == args.output.resolve():
        raise ValueError("input and output SQLite paths must be different")

    rows = load_rows(args.input)
    if not rows:
        raise ValueError("legacy SQLite index is empty")
    repository = LocalHybridRetriever(
        args.output,
        chroma_dir=args.chroma_dir,
        embedding_function=ClovaEmbeddings(),
    )
    repository.write_rows_with_embeddings(rows)
    issues = repository.readiness_issues()
    if issues:
        raise RuntimeError("migrated index is not ready: " + "; ".join(issues))
    print(f"migrated_rows={len(rows)}")
    print(f"documents={len({row['doc_id'] for row in rows})}")
    print("embedding_dimension=1024")
    print(f"output={args.output}")
    print(f"chroma_dir={args.chroma_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
