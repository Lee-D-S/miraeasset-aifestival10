import argparse
import json

from rag.clients.embedding import EmbeddingClient
from rag.storage.local import LocalVectorStore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search a local CLOVA Embedding v2 vector index."
    )
    parser.add_argument("question")
    parser.add_argument("--index", default="vector_db/disclosure_clova_local.json")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args()

    store = LocalVectorStore.load(args.index)
    if not len(store):
        raise RuntimeError(f"Vector index is empty or missing: {args.index}")

    query_embedding = EmbeddingClient().embed_text(args.question)
    results = store.search(query_embedding.vector, limit=args.top_k)
    print(
        json.dumps(
            {
                "question": args.question,
                "query_input_tokens": query_embedding.input_tokens,
                "index_size": len(store),
                "results": [
                    {
                        "id": row["id"],
                        "score": row["score"],
                        "text": row["text"],
                        "source_path": row["source_path"],
                        "corp_name": row.get("corp_name", ""),
                        "document_type": row.get("document_type", ""),
                        "report_period": row.get("report_period", ""),
                        "chunk_index": row.get("chunk_index", ""),
                    }
                    for row in results
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
