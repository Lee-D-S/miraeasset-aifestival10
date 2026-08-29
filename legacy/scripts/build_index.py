import argparse
import json

from common.config import settings
from rag.ingestion.pipeline import CorpusScanner
from rag.services.indexing_service import IndexingService
from rag.storage.postgres import PostgresStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the approved disclosure vector index.")
    parser.add_argument("--source", default=settings.source_root)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not args.source:
        parser.error("--source or RAG_SOURCE_ROOT is required")
    if not settings.postgres_dsn:
        parser.error("POSTGRES_DSN is required")

    store = PostgresStore(settings.postgres_dsn)
    store.initialize()
    report = IndexingService(CorpusScanner(args.source), store).run(limit=args.limit)
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
