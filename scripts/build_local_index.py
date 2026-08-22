import argparse
import json
from datetime import date

from rag.config import settings
from rag.ingestion.pipeline import CorpusScanner
from rag.services.fake_embedding import FakeEmbeddingClient
from rag.storage.local import LocalVectorRow, LocalVectorStore


def _metadata(document) -> dict[str, str]:
    return {
        "corp_name": document.corp_name,
        "corp_code": document.corp_code,
        "document_type": document.document_type,
        "market": document.market,
        "report_period": document.report_period,
        "disclosure_date": document.disclosure_date.isoformat() if isinstance(document.disclosure_date, date) else "",
        "source_group": document.source_group,
        "source_hash": document.source_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a no-cost local vector index from disclosure documents.")
    parser.add_argument("--source", default=settings.source_root, required=not bool(settings.source_root))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", default="vector_db/disclosure_local.json")
    parser.add_argument("--question", default="매출과 영업이익에 대한 공시 내용을 알려줘")
    args = parser.parse_args()

    documents, errors = CorpusScanner(args.source).scan(limit=args.limit)
    embedder = FakeEmbeddingClient()
    store = LocalVectorStore()
    rows = [
        LocalVectorRow(
            id=document.document_id,
            text=document.text,
            source_path=str(document.source_path),
            embedding=embedder.embed_text(document.text).vector,
            metadata=_metadata(document),
        )
        for document in documents
    ]
    store.upsert(rows)
    store.save(args.output)
    results = store.search(embedder.embed_text(args.question).vector, limit=min(5, len(store)))
    print(json.dumps({
        "indexed_documents": len(store),
        "output": args.output,
        "scan_errors": errors,
        "results": [
            {
                "id": row["id"],
                "score": row["score"],
                "corp_name": row.get("corp_name", ""),
                "report_period": row.get("report_period", ""),
                "source_path": row["source_path"],
            }
            for row in results
        ],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
