"""Rebuild the Retriever index delta for a small set of doc_ids.

Used to fix the 37 사업보고서 whose 재무제표 챕터 was lost by the pre-fix DART
XML parser (see ``docs/reindex-37docs.md``). Produces a SQLite + Chroma delta
that ``merge_delta_into_production.py`` merges into the server's production
index.

Parsing/chunking uses the repo's ``build_dart_chunk_rows`` (identical to the
serving/build contract). Embeddings use ``sentence-transformers`` with
``intfloat/multilingual-e5-large`` -- ``"passage: "`` prefix, mean pooling,
L2 normalize -- exactly as ``scripts/colab_build_chunk_index.py`` built the
production document vectors, so the delta lands in the same cosine space.

Example::

    .venv/bin/python scripts/reindex_37_delta.py \
      --corpus-dir /home/user/contest/reindex_work/corpus37 \
      --doc-ids   /home/user/contest/reindex_work/affected_37_doc_ids.json \
      --out-dir   /home/user/contest/reindex_work/delta_build
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MODEL_NAME = "intfloat/multilingual-e5-large"
COLLECTION = "chunk_vectors"
EMBEDDING_DIM = 1024
MAX_CHUNK_LEN = 1000
EMBED_BATCH = 128
UPSERT_BATCH = 1000


def _load_doc_ids(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise SystemExit(f"{path} must be a JSON list of doc_id strings")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus-dir", type=Path, required=True)
    parser.add_argument("--doc-ids", type=Path, required=True, help="JSON list of doc_id")
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--fp16", dest="fp16", action="store_true", default=True,
                        help="half precision on CUDA (matches colab_build_chunk_index.py FP16=True)")
    parser.add_argument("--no-fp16", dest="fp16", action="store_false")
    parser.add_argument("--max-chunk-len", type=int, default=MAX_CHUNK_LEN)
    args = parser.parse_args()

    doc_ids = _load_doc_ids(args.doc_ids)
    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    sqlite_path = out_dir / "chunk_index_delta.db"
    chroma_dir = out_dir / "chunk_index_chroma_delta"
    if sqlite_path.exists():
        sqlite_path.unlink()

    # 1) parse + chunk -----------------------------------------------------
    from retriever.ingestion import build_dart_chunk_rows

    print(f"parsing {len(doc_ids)} documents from {args.corpus_dir} ...", flush=True)
    t0 = time.time()
    rows = build_dart_chunk_rows(
        args.corpus_dir,
        doc_ids=doc_ids,
        max_chunk_len=args.max_chunk_len,
    )
    if not rows:
        raise SystemExit("no chunk rows produced -- check --corpus-dir / --doc-ids")
    got_docs = sorted({str(r.get("doc_id")) for r in rows})
    print(f"  {len(rows)} chunk rows from {len(got_docs)} docs in {time.time() - t0:.1f}s", flush=True)
    missing = sorted(set(doc_ids) - set(got_docs))
    if missing:
        raise SystemExit("no chunks for: " + ", ".join(missing))

    # 2) write SQL rows --------------------------------------------------
    from retriever.backends import local_sqlite_engine
    from retriever.ingestion.writer import _chroma_record, write_rows

    engine = local_sqlite_engine(sqlite_path)
    write_rows(engine, rows)  # no vectorstore: SQL only
    engine.dispose()
    print(f"  wrote {sqlite_path.name}", flush=True)

    # 3) embed -----------------------------------------------------------
    from sentence_transformers import SentenceTransformer

    print(f"loading {MODEL_NAME} on {args.device} (fp16={args.fp16}) ...", flush=True)
    model = SentenceTransformer(MODEL_NAME, device=args.device)
    if args.fp16 and args.device == "cuda":
        model = model.half()

    records = [_chroma_record(row) for row in rows]  # (chunk_id, text, metadata)
    ids = [rec[0] for rec in records]
    texts = [rec[1] for rec in records]
    metadatas = [rec[2] for rec in records]
    if len(set(ids)) != len(ids):
        raise SystemExit("duplicate chunk_id in rows")

    print(f"embedding {len(texts)} chunks ...", flush=True)
    t0 = time.time()
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = texts[start:start + EMBED_BATCH]
        vecs = model.encode(
            ["passage: " + str(t) for t in batch],
            normalize_embeddings=True,
            batch_size=EMBED_BATCH,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors.extend(v.tolist() for v in vecs)
        seen = min(start + EMBED_BATCH, len(texts))
        rate = seen / max(time.time() - t0, 1e-9)
        eta = (len(texts) - seen) / max(rate, 1e-9) / 60
        print(f"  {seen}/{len(texts)} · {rate:.0f}/s · ETA {eta:.1f} min", flush=True)
    if any(len(v) != EMBEDDING_DIM for v in vectors):
        raise SystemExit("embedding dim mismatch")

    # 4) write Chroma delta -------------------------------------------
    import chromadb

    client = chromadb.PersistentClient(path=str(chroma_dir))
    try:
        client.delete_collection(COLLECTION)
    except Exception:
        pass
    col = client.get_or_create_collection(COLLECTION, metadata={"hnsw:space": "cosine"})
    for start in range(0, len(ids), UPSERT_BATCH):
        sl = slice(start, start + UPSERT_BATCH)
        col.upsert(
            ids=ids[sl],
            embeddings=vectors[sl],
            documents=texts[sl],
            metadatas=metadatas[sl],
        )
        print(f"  upsert {min(start + UPSERT_BATCH, len(ids))}/{len(ids)}", flush=True)

    # 5) verify --------------------------------------------------------
    # Page the read: an unbounded col.get() over ~100k rows trips chromadb
    # 1.5.9's "too many SQL variables" on the sqlite side.
    stored_ids: set[str] = set()
    stored_docs: set[str] = set()
    total = col.count()
    for off in range(0, total, 5000):
        page = col.get(limit=5000, offset=off, include=["metadatas"])
        if not page["ids"]:
            break
        stored_ids.update(page["ids"])
        stored_docs.update(m.get("doc_id") for m in page["metadatas"])
    if stored_ids != set(ids):
        raise SystemExit(
            f"Chroma id set mismatch: stored={len(stored_ids)} expected={len(ids)}"
        )
    if stored_docs != set(doc_ids):
        raise SystemExit(f"Chroma doc_id set mismatch: {sorted(stored_docs)}")
    probe = col.get(ids=ids[:3], include=["embeddings"])
    if probe["embeddings"] is None or len(probe["embeddings"][0]) != EMBEDDING_DIM:
        raise SystemExit("Chroma probe get() failed")

    cx = sqlite3.connect(str(sqlite_path))
    sql_rows = cx.execute("SELECT count(*) FROM chunk_index").fetchone()[0]
    sql_docs = cx.execute("SELECT count(DISTINCT doc_id) FROM chunk_index").fetchone()[0]
    samsung_income = cx.execute(
        "SELECT count(*) FROM chunk_index WHERE doc_id='periodic_20250311001085' "
        "AND (text LIKE '%손익계산서%' OR text LIKE '%영업이익%')"
    ).fetchone()[0]
    cx.close()

    print("--- delta summary ---", flush=True)
    print(f"sql_rows={sql_rows} sql_docs={sql_docs}")
    print(f"chroma_vectors={total} chroma_docs={len(stored_docs)}")
    print(f"samsung2024_income_or_op_profit_chunks={samsung_income}")
    print(f"sqlite={sqlite_path}")
    print(f"chroma_dir={chroma_dir}")
    if sql_rows != len(ids) or col.count() != len(ids) or sql_docs != len(doc_ids):
        raise SystemExit("count mismatch between rows / SQL / Chroma")
    if samsung_income == 0:
        print("WARNING: Samsung 2024 still has no income-statement text", flush=True)
    print("OK", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
