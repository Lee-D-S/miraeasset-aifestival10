from __future__ import annotations

from typing import Protocol


class EmbeddingPort(Protocol):
    def embed(self, text: str) -> list[float]: ...


class EmbeddingAdapter:
    """Boundary for CLOVA Embedding v2 or a deterministic test implementation."""

    def __init__(self, embedder):
        self.embedder = embedder

    def embed(self, text: str) -> list[float]:
        result = self.embedder.embed_text(text) if hasattr(self.embedder, "embed_text") else self.embedder(text)
        return list(getattr(result, "vector", result))

