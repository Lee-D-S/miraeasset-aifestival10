import argparse
import json

from common.config import settings
from langgraph_rag.indexing import build_postgres_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the PostgreSQL vector index for the LangGraph backend.")
    parser.add_argument("--source", default=settings.source_root)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    if not args.source:
        parser.error("--source or RAG_SOURCE_ROOT is required")
    if not settings.postgres_dsn:
        parser.error("POSTGRES_DSN is required")
    report = build_postgres_index(args.source, settings.postgres_dsn, limit=args.limit)
    print(json.dumps(report.__dict__, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
