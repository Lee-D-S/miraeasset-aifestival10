from __future__ import annotations

import json
import hashlib
from typing import Protocol
from urllib.request import Request, urlopen
from agentic_rag.infrastructure.retry import retry_call


class EmbeddingPort(Protocol):
    def embed(self, text: str) -> list[float]: ...


class EmbeddingAdapter:
    """Boundary for CLOVA Embedding v2 or a deterministic test implementation."""

    def __init__(self, embedder):
        self.embedder = embedder

    def embed(self, text: str) -> list[float]:
        result = self.embedder.embed_text(text) if hasattr(self.embedder, "embed_text") else self.embedder(text)
        return list(getattr(result, "vector", result))


class ClovaEmbedding:
    def embed(self, text: str) -> list[float]:
        from common.config import settings
        if not settings.clova_api_key:
            raise RuntimeError("CLOVA_API_KEY is required for PostgreSQL embedding search")
        request = Request(
            f"https://{settings.clova_api_host}/v1/api-tools/embedding/v2",
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {settings.clova_api_key}"},
            method="POST",
        )
        with retry_call(lambda: urlopen(request, timeout=120)) as response:
            body = json.loads(response.read().decode("utf-8"))
        vector = body.get("result", {}).get("embedding", [])
        if not vector:
            raise RuntimeError("CLOVA returned an empty embedding")
        return vector


class DeterministicEmbedding:
    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(32)]
