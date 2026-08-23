import argparse
import json
import sys

from rag.clients.embedding import EmbeddingClient
from rag.clients.segmentation import SegmentationClient
from rag.config import settings
from rag.ingestion.pipeline import CorpusScanner


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test CLOVA segmentation followed by one Embedding v2 request."
    )
    parser.add_argument(
        "--source", default=settings.source_root, required=not bool(settings.source_root)
    )
    parser.add_argument("--max-chars", type=int, default=20_000)
    parser.add_argument("--paragraph-index", type=int, default=0)
    args = parser.parse_args()

    documents, errors = CorpusScanner(args.source).scan(limit=1)
    if errors:
        raise RuntimeError(f"Corpus scan failed: {errors[0]}")
    if not documents:
        raise RuntimeError("No document found")

    document = documents[0]
    text = document.text[: args.max_chars]
    segmentation = SegmentationClient().segment_text(text)
    if not segmentation.paragraphs:
        raise RuntimeError("Segmentation returned no paragraphs")
    if args.paragraph_index >= len(segmentation.paragraphs):
        raise ValueError(
            f"paragraph-index must be between 0 and {len(segmentation.paragraphs) - 1}"
        )

    paragraph = segmentation.paragraphs[args.paragraph_index]
    embedding = EmbeddingClient().embed_text(paragraph)
    print(
        json.dumps(
            {
                "document_id": document.document_id,
                "paragraph_index": args.paragraph_index,
                "paragraph_characters": len(paragraph),
                "paragraph_preview": paragraph[:300],
                "embedding_dimension": len(embedding.vector),
                "embedding_input_tokens": embedding.input_tokens,
                "embedding_preview": embedding.vector[:5],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
