import argparse
import json

from common.config import settings
from langgraph_rag.indexing import build_local_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a local CLOVA vector index for the LangGraph backend.")
    parser.add_argument("--source", default=settings.source_root, required=not bool(settings.source_root))
    parser.add_argument("--limit", type=int, default=1)
    parser.add_argument("--max-chars", type=int, default=20_000)
    parser.add_argument("--chunk-start", type=int, default=0)
    parser.add_argument("--max-chunks", type=int, default=3)
    parser.add_argument("--segmentation-cache", default="test_data/langgraph_segmentation_cache.json")
    parser.add_argument("--output", default="test_data/langgraph_disclosure_clova_local.json")
    args = parser.parse_args()
    print(json.dumps(build_local_index(
        args.source,
        limit=args.limit,
        max_chars=args.max_chars,
        chunk_start=args.chunk_start,
        max_chunks=args.max_chunks,
        segmentation_cache=args.segmentation_cache,
        output=args.output,
    ), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
