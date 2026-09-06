"""Build read-only sidecar artifacts for retrieval experiments."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from retriever.retrieval_experiments import (
    DEFAULT_NLIST,
    DEFAULT_PQ_M,
    DEFAULT_PQ_NBITS,
    build_vector_sidecars,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--index-root",
        default=os.getenv("EXPERIMENT_INDEX_ROOT", str(config.LOCAL_DB_DIR)),
    )
    parser.add_argument(
        "--artifact-root",
        default=os.getenv(
            "EXPERIMENT_ARTIFACT_ROOT",
            str(config.DATA_DIR / "retrieval_experiments" / "supplied"),
        ),
    )
    parser.add_argument("--collection", default=config.CHROMA_COLLECTION)
    parser.add_argument("--table", default=config.SQLITE_TABLE)
    parser.add_argument("--nlist", type=int, default=DEFAULT_NLIST)
    parser.add_argument("--pq-m", type=int, default=DEFAULT_PQ_M)
    parser.add_argument("--pq-nbits", type=int, default=DEFAULT_PQ_NBITS)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument(
        "--skip-faiss",
        action="store_true",
        help="build vectors and FTS5 only when faiss-cpu is not installed",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    if args.nlist <= 0 or args.batch_size <= 0:
        parser.error("--nlist and --batch-size must be positive")
    try:
        report = build_vector_sidecars(
            index_root=args.index_root,
            artifact_root=args.artifact_root,
            collection_name=args.collection,
            table_name=args.table,
            nlist=args.nlist,
            pq_m=args.pq_m,
            pq_nbits=args.pq_nbits,
            batch_size=args.batch_size,
            build_faiss=not args.skip_faiss,
            overwrite=args.overwrite,
        )
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"NOT READY: {type(error).__name__}: {error}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
