from dataclasses import dataclass

from rag.clients.base import ClovaApiClient


@dataclass(frozen=True)
class RerankerResult:
    result: str
    cited_documents: list[dict]
    suggested_queries: list[str]
    usage: dict


class RerankerClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def rerank_documents(self, query: str, documents: list[dict], *, max_tokens: int = 1024) -> RerankerResult:
        if not documents:
            return RerankerResult("", [], [], {})
        normalized = [{"id": str(item["id"]), "doc": str(item["doc"])} for item in documents]
        result = self.api.result_or_raise(self.api.post("/v1/api-tools/reranker", {
            "documents": normalized, "query": query, "maxTokens": max_tokens,
        }), "reranker")
        suggested = result.get("suggestedQueries", []) or []
        if isinstance(suggested, str):
            suggested = [suggested]
        return RerankerResult(result.get("result", ""), result.get("citedDocuments", []), suggested, result.get("usage", {}))

