import argparse
import json
from datetime import date

from common.config import settings
from langgraph_rag.ingestion import CorpusScanner
from langgraph_rag.runtime import FakeEmbeddingClient, LocalVectorRow, LocalVectorStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a no-cost local index for LangGraph smoke tests.")
    parser.add_argument("--source", default=settings.source_root, required=not bool(settings.source_root))
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--output", default="test_data/langgraph_disclosure_fake_local.json")
    args = parser.parse_args()
    documents, errors = CorpusScanner(args.source).scan(limit=args.limit)
    embedder = FakeEmbeddingClient()
    store = LocalVectorStore()
    store.upsert([
        LocalVectorRow(
            id=document.document_id,
            text=document.text,
            source_path=str(document.source_path),
            embedding=embedder.embed_text(document.text).vector,
            metadata={
                "corp_name": document.corp_name,
                "corp_code": document.corp_code,
                "document_type": document.document_type,
                "market": document.market,
                "report_period": document.report_period,
                "disclosure_date": document.disclosure_date.isoformat() if isinstance(document.disclosure_date, date) else "",
                "source_group": document.source_group,
            },
        )
        for document in documents
    ])
    store.save(args.output)
    print(json.dumps({"indexed_documents": len(store), "output": args.output, "scan_errors": errors}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
