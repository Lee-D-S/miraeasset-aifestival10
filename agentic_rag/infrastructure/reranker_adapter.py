import json
from typing import Any, Protocol
from urllib.request import Request, urlopen


class RerankerPort(Protocol):
    def rerank(self, query: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]: ...


class RerankerAdapter:
    def __init__(self, reranker):
        self.reranker = reranker

    def rerank(self, query: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.reranker.rerank(query, documents) if hasattr(self.reranker, "rerank") else self.reranker(query, documents)


class ClovaReranker:
    """Independent CLOVA Reranker adapter with deterministic failure fallback."""

    def rerank(self, query: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not documents:
            return []
        from common.config import settings
        if not settings.clova_api_key:
            return sorted(documents, key=lambda item: float(item.get("score", 0)), reverse=True)
        payload = {"documents": [{"id": str(item.get("id", "")), "doc": str(item.get("text", ""))} for item in documents], "query": query, "maxTokens": 1024}
        try:
            request = Request(
                f"https://{settings.clova_api_host}/v1/api-tools/reranker",
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {settings.clova_api_key}"},
                method="POST",
            )
            with urlopen(request, timeout=120) as response:
                body = json.loads(response.read().decode("utf-8"))
            cited_ids = [str(item.get("id", "")) for item in body.get("result", {}).get("citedDocuments", [])]
            by_id = {str(item.get("id", "")): item for item in documents}
            cited = [by_id[item_id] for item_id in cited_ids if item_id in by_id]
            return cited or sorted(documents, key=lambda item: float(item.get("score", 0)), reverse=True)
        except Exception:
            return sorted(documents, key=lambda item: float(item.get("score", 0)), reverse=True)
