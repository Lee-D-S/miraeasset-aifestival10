import argparse
import json

from langgraph_rag.runtime import DocumentReranker, EmbeddingClient, LocalVectorStore, VectorRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Search a local vector index owned by the LangGraph backend.")
    parser.add_argument("question")
    parser.add_argument("--index", default="test_data/langgraph_disclosure_clova_local.json")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--rerank-top-k", type=int, default=5)
    args = parser.parse_args()
    store = LocalVectorStore.load(args.index)
    if not len(store):
        raise RuntimeError(f"Vector index is empty or missing: {args.index}")
    retriever = VectorRetriever(store, EmbeddingClient(), args.top_k)
    documents = retriever.search(args.question, limit=args.top_k)
    reranked = DocumentReranker().rerank(args.question, documents[:args.rerank_top_k])
    print(json.dumps({
        "question": args.question,
        "index_size": len(store),
        "results": [document.model_dump() for document in documents],
        "cited_documents": [document.model_dump() for document in reranked.documents],
        "suggested_queries": reranked.suggested_queries,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
