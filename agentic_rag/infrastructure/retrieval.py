from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


class LocalVectorRetriever:
    """Independent local adapter for the existing JSON index shape."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self.rows = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else []

    def corp_names(self) -> list[str]:
        return sorted({str(row.get("metadata", {}).get("corp_name", "")) for row in self.rows if row.get("metadata", {}).get("corp_name")})

    def search(self, query: str, limit: int = 20, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        dimension = next((len(row.get("embedding", [])) for row in self.rows if row.get("embedding")), 1024)
        query_vector = self._query_vector(query, dimension)
        scored = []
        for row in self.rows:
            metadata = row.get("metadata", {})
            if filters and any(value and str(metadata.get(key, "")) != value and (key != "document_type" or value not in str(metadata.get(key, ""))) for key, value in filters.items()):
                continue
            score = self._cosine(query_vector, row.get("embedding", []))
            scored.append({"id": str(row.get("id", "")), "source": str(row.get("source_path", row.get("source", ""))), "text": str(row.get("text", "")), "score": max(0.0, min(1.0, score)), "metadata": metadata})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    @staticmethod
    def _vector(text: str, dimension: int = 1024) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[i % len(digest)] / 255) * 2) - 1 for i in range(dimension)]

    @classmethod
    def _query_vector(cls, text: str, dimension: int) -> list[float]:
        """Use CLOVA Embedding v2 when configured; retain a no-cost test fallback."""
        try:
            from common.config import settings
            if settings.clova_api_key:
                payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
                request = Request(
                    f"https://{settings.clova_api_host}/v1/api-tools/embedding/v2",
                    data=payload,
                    headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {settings.clova_api_key}"},
                    method="POST",
                )
                with urlopen(request, timeout=120) as response:
                    body = json.loads(response.read().decode("utf-8"))
                vector = body.get("result", {}).get("embedding", [])
                if vector:
                    return vector
        except Exception:
            pass
        return cls._vector(text, dimension)

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if not right or len(left) != len(right):
            return 0.0
        norm_left, norm_right = math.sqrt(sum(x * x for x in left)), math.sqrt(sum(x * x for x in right))
        return sum(a * b for a, b in zip(left, right)) / (norm_left * norm_right) if norm_left and norm_right else 0.0


class RetrieverAdapter:
    def __init__(self, retriever: Any):
        self.retriever = retriever

    def search(self, query: str, limit: int, filters: dict[str, str]) -> list[dict[str, Any]]:
        return self.retriever.search(query, limit=limit, filters=filters)

    def corp_names(self) -> list[str]:
        return self.retriever.corp_names()
