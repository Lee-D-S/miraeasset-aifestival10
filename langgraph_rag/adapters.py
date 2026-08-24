from pathlib import Path
from typing import Any

from rag.clients.rag_reasoning import RagReasoningClient
from rag.retrieval.rerank import DocumentReranker
from rag.retrieval.vector_search import VectorRetriever
from rag.schemas import RetrievedDocument
from rag.storage.local import LocalVectorStore
from rag.storage.postgres import PostgresStore

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
    )
