from pathlib import Path
from typing import Any

from rag.clients.rag_reasoning import RagReasoningClient
from rag.retrieval.rerank import DocumentReranker
from rag.retrieval.vector_search import VectorRetriever
from common.schemas import RetrievedDocument
from rag.storage.local import LocalVectorStore
from rag.storage.postgres import PostgresStore
from rag.retrieval.alternatives import AlternativeFinder

from langgraph_rag.contracts import GraphDependencies


class ExistingRetrieverAdapter:
    def __init__(self, retriever: VectorRetriever, limit: int) -> None:
        self.retriever = retriever
        self.limit = limit

    def search(self, query: str, *, limit: int | None = None) -> list[dict[str, Any]]:
        return [document.model_dump() for document in self.retriever.search(query, limit=limit or self.limit)]


class ExistingRerankerAdapter:
    def __init__(self, reranker: DocumentReranker) -> None:
        self.reranker = reranker

    def rerank(self, query: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        typed_documents = [RetrievedDocument.model_validate(document) for document in documents]
        result = self.reranker.rerank(query, typed_documents)
        return {
            "answer": result.answer,
            "documents": [document.model_dump() for document in result.documents],
            "suggested_queries": result.suggested_queries,
        }


class ExistingLlmAdapter:
    def __init__(self, client: RagReasoningClient) -> None:
        self.client = client

    def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        return self.client.generate(messages, tools)


class ExistingAlternativeFinderAdapter:
    def __init__(self, finder: AlternativeFinder) -> None:
        self.finder = finder

    def find(self, question: str) -> dict[str, list[dict[str, Any]]]:
        return self.finder.find(question).as_dict()


def build_dependencies(settings: Any) -> GraphDependencies | None:
    if not settings.clova_api_key:
        return None
    if settings.postgres_dsn:
        store = PostgresStore(settings.postgres_dsn)
    elif Path(settings.local_vector_index).exists():
        store = LocalVectorStore.load(settings.local_vector_index)
    else:
        return None
    from rag.clients.embedding import EmbeddingClient
    from rag.clients.reranker import RerankerClient

    retriever = VectorRetriever(store, EmbeddingClient(), settings.retrieval_top_k)
    return GraphDependencies(
        retriever=ExistingRetrieverAdapter(retriever, settings.retrieval_top_k),
        reranker=ExistingRerankerAdapter(DocumentReranker(RerankerClient())),
        llm=ExistingLlmAdapter(RagReasoningClient()),
        alternative_finder=ExistingAlternativeFinderAdapter(AlternativeFinder(retriever)),
    )
