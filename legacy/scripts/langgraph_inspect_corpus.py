import argparse
import json

from langgraph_rag.ingestion import CorpusScanner


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect the approved disclosure corpus for the LangGraph backend.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    records, errors = CorpusScanner(args.source).scan(limit=args.limit)
    print(json.dumps({
        "documents": len(records),
        "errors": len(errors),
        "total_characters": sum(len(record.text) for record in records),
        "sample_ids": [record.document_id for record in records[:5]],
        "error_samples": errors[:10],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
