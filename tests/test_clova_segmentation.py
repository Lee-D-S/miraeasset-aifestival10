import argparse
import json
import sys

from common.config import settings
from rag.ingestion.pipeline import CorpusScanner
from rag.clients.segmentation import SegmentationClient


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Test CLOVA segmentation with one disclosure document.")
    parser.add_argument("--source", default=settings.source_root, required=not bool(settings.source_root))
    parser.add_argument("--max-chars", type=int, default=20_000)
    args = parser.parse_args()

    documents, errors = CorpusScanner(args.source).scan(limit=1)
    if errors:
        raise RuntimeError(f"Corpus scan failed: {errors[0]}")
    if not documents:
        raise RuntimeError("No document found")

    document = documents[0]
    test_text = document.text[:args.max_chars]
    result = SegmentationClient().segment_text(test_text)
    print(json.dumps({
        "document_id": document.document_id,
        "source_path": str(document.source_path),
        "input_characters": len(test_text),
        "paragraph_count": len(result.paragraphs),
        "input_tokens": result.input_tokens,
        "paragraph_samples": [paragraph[:300] for paragraph in result.paragraphs[:3]],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
