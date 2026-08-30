"""Build the local SQLite Stage2 index from an embedded chunk JSON file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stage2.local_store import LocalHybridRetriever


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/local_smoke/embedded_chunks.json"))
    parser.add_argument("--selection", type=Path, default=Path("data/local_smoke/selected_documents.json"))
    parser.add_argument("--output", type=Path, default=Path("data/local_smoke/smoke.db"))
    parser.add_argument("--chroma-dir", type=Path, default=Path("data/local_smoke/smoke_chroma"))
    args = parser.parse_args()

    rows = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("input must contain a JSON list of embedded chunks")
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    selected_by_id = {
        str(document["doc_id"]): document
        for document in selection.get("documents", [])
        if isinstance(document, dict) and document.get("doc_id")
    }
    for row in rows:
        selected = selected_by_id.get(str(row.get("doc_id")), {})
        if selected:
            row["metadata"] = {**selected, **dict(row.get("metadata", {}))}
    # Chroma now computes and stores embeddings itself via its
    # embedding_function, so any precomputed "embedding" field on a row is
    # ignored -- building this index requires a live embedding provider
    # (CLOVA, by default) instead of reusing offline-precomputed vectors.
    repository = LocalHybridRetriever(args.output, chroma_dir=args.chroma_dir)
    repository.write_rows(rows)
    print(f"stored_rows={len(rows)}")
    print(f"output={args.output}")
    print(f"chroma_dir={args.chroma_dir}")


if __name__ == "__main__":
    main()
