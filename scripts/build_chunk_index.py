"""Build the ``chunk_index`` SQLite + Chroma store from a raw DART corpus.

The local equivalent of the Colab notebook cell: parse + chunk disclosures
with :mod:`retriever.ingestion.dart`, then persist through
:mod:`retriever.ingestion.writer` into the local SQLite and Chroma index. Serving
opens the result read-only (:class:`retriever.local_store.LocalHybridRetriever`);
this script is the only writer.

Examples
--------
Whole corpus, local e5 embeddings::

    python scripts/build_chunk_index.py --corpus-dir data/3.gongsi/corpus

"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from retriever.backends import local_chroma, local_sqlite_engine
from retriever.ingestion import build_dart_chunk_rows
from retriever.ingestion.writer import CHUNK_TABLE, write_rows
from retriever.local_store import LocalHybridRetriever


def _embedding_function(name: str):
    if name == "e5":
        from retriever.embedding import E5Embeddings

        return E5Embeddings()
    raise SystemExit(f"unknown --embedding {name!r}; choose e5")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--corpus-dir",
        type=Path,
        required=True,
        help="Directory with universe.csv, manifest.jsonl and the raw/ document tree",
    )
    parser.add_argument(
        "--selection",
        type=Path,
        default=None,
        help="Optional selected_documents.json; index only its doc_ids",
    )
    parser.add_argument("--doc-id", action="append", default=[], help="Index only these doc_ids (repeatable)")
    parser.add_argument("--sqlite", type=Path, default=config.SQLITE_PATH)
    parser.add_argument("--chroma-dir", type=Path, default=config.CHROMA_PATH)
    parser.add_argument("--collection-name", default=config.CHROMA_COLLECTION)
    parser.add_argument("--embedding", choices=("e5",), default="e5")
    parser.add_argument("--max-chunk-len", type=int, default=1000)
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parse/chunk processes over documents: 1 = sequential (default), 0 = all CPU cores, N = N processes",
    )
    args = parser.parse_args()

    rows = build_dart_chunk_rows(
        args.corpus_dir,
        doc_ids=args.doc_id or None,
        selection_path=args.selection,
        max_chunk_len=args.max_chunk_len,
        max_workers=args.workers or None,
    )
    if not rows:
        raise SystemExit("no chunk rows produced; check --corpus-dir / --selection")

    engine = local_sqlite_engine(args.sqlite)
    vectorstore = local_chroma(
        args.chroma_dir,
        embedding_function=_embedding_function(args.embedding),
        collection_name=args.collection_name,
        create_directory=True,
    )
    write_rows(engine, rows, vectorstore=vectorstore)

    retriever = LocalHybridRetriever(
        engine=engine,
        vectorstore=vectorstore,
        collection_name=args.collection_name,
        table_name=CHUNK_TABLE,
        read_only=True,
    )
    issues = retriever.readiness_issues()
    if issues:
        raise SystemExit("index is not ready: " + "; ".join(issues))

    print(f"table={CHUNK_TABLE}")
    print(f"rows={len(rows)}")
    print(f"documents={len({row['doc_id'] for row in rows})}")
    print(f"tables_with_json={sum(1 for row in rows if row.get('raw_json_content'))}")
    print(f"embedding={args.embedding}")
    print(f"sqlite={args.sqlite}")
    print(f"chroma_dir={args.chroma_dir}")
    print(f"collection={args.collection_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
