from __future__ import annotations

import argparse

from agentic_rag.infrastructure.embedding_adapter import ClovaEmbedding, DeterministicEmbedding
from agentic_rag.infrastructure.postgres import PostgresIndexWriter
from agentic_rag.infrastructure.segmentation_adapter import ClovaSegmentation
from agentic_rag.ingestion.pipeline import build_local_index
from common.config import settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an agentic_rag local index from the supplied corpus")
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", default="test_data/agentic_rag_local.json")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--postgres-dsn", default="")
    parser.add_argument("--failure-log", default="agentic_rag_ingestion_failures.json")
    args = parser.parse_args()
    embedder = ClovaEmbedding() if settings.clova_api_key else DeterministicEmbedding()
    segmenter = ClovaSegmentation() if settings.clova_api_key else None
    writer = PostgresIndexWriter(args.postgres_dsn) if args.postgres_dsn else None
    count = build_local_index(args.source, args.output, embedder=embedder, segmenter=segmenter, writer=writer, limit=args.limit, failure_log=args.failure_log)
    print(f"indexed_chunks={count}")


if __name__ == "__main__":
    main()
