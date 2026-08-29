import argparse
import json
import sys

from rag.clients.embedding import EmbeddingClient
from rag.clients.rag_reasoning import RagReasoningClient
from rag.clients.reranker import RerankerClient
from rag.generation.answer_generator import RagAnswerGenerator
from rag.retrieval.rerank import DocumentReranker, RerankedResult
from rag.retrieval.vector_search import VectorRetriever
from rag.storage.local import LocalVectorStore


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run local vector search, reranking, and CLOVA RAG Reasoning."
    )
    parser.add_argument("question")
    parser.add_argument("--index", default="test_data/disclosure_clova_local.json")
    parser.add_argument("--retrieval-top-k", type=int, default=5)
    parser.add_argument("--rerank-top-k", type=int, default=5)
    args = parser.parse_args()

    store = LocalVectorStore.load(args.index)
    if not len(store):
        print(json.dumps(_fallback(args.question, "로컬 Vector Store가 비어 있습니다."), ensure_ascii=False, indent=2))
        return

    retriever = VectorRetriever(store, EmbeddingClient(), args.retrieval_top_k)
    reranker = DocumentReranker(RerankerClient())

    def search(query: str) -> RerankedResult:
        documents = retriever.search(query, limit=args.retrieval_top_k)
        return reranker.rerank(query, documents[: args.rerank_top_k])

    initial_documents = retriever.search(args.question, limit=args.retrieval_top_k)
    initial_reranked = reranker.rerank(
        args.question,
        initial_documents[: args.rerank_top_k],
    )
    if not initial_reranked.documents:
        print(json.dumps(_fallback(
            args.question,
            initial_reranked.answer or "由щ옲而ㅻ 寃利앹쓣 ?듦븯??寃곌낵媛 ?놁뒿?덈떎.",
        ), ensure_ascii=False, indent=2))
        return

    generated = RagAnswerGenerator(RagReasoningClient()).generate(
        args.question,
        search,
        initial_documents=initial_reranked.documents,
    )
    if not generated.documents:
        print(json.dumps(_fallback(
            args.question,
            generated.think_trace or "관련 공시 근거를 찾지 못했습니다.",
        ), ensure_ascii=False, indent=2))
        return

    print(json.dumps({
        "question": args.question,
        "answer": generated.answer,
        "think_trace": generated.think_trace,
        "cited_documents": [
            {
                "id": document.id,
                "source_path": document.source,
                "text": document.text,
                "metadata": document.metadata,
            }
            for document in generated.documents
        ],
    }, ensure_ascii=False, indent=2))


def _fallback(question: str, trace: str) -> dict[str, str]:
    return {
        "question": question,
        "answer": "제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.",
        "think_trace": trace,
        "cited_documents": [],
    }


if __name__ == "__main__":
    main()
