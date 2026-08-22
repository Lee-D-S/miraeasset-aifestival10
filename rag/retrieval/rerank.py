from dataclasses import dataclass

from rag.clients.reranker import RerankerClient
from rag.schemas import RetrievedDocument


@dataclass
class RerankedResult:
    answer: str
    documents: list[RetrievedDocument]
    suggested_queries: list[str]


class DocumentReranker:
    def __init__(self, client: RerankerClient | None = None) -> None:
        self.client = client or RerankerClient()

    def rerank(
        self,
        query: str,
        documents: list[RetrievedDocument],
        *,
        max_tokens: int = 1024,
    ) -> RerankedResult:
        if not documents:
            return RerankedResult("", [], [])
        response = self.client.rerank_documents(
            query,
            [{"id": document.id, "doc": document.text} for document in documents],
            max_tokens=max_tokens,
        )
        by_id = {document.id: document for document in documents}
        cited = []
        for item in response.cited_documents:
            document_id = str(item.get("id", ""))
            if document_id in by_id:
                cited.append(by_id[document_id])
        return RerankedResult(response.result, cited, response.suggested_queries)
