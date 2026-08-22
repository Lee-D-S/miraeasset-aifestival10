import hashlib

from rag.clients.embedding import EmbeddingResult


class FakeEmbeddingClient:
    """Deterministic local embedding substitute for no-cost smoke tests."""

    dimension = 1024

    def embed_text(self, text: str) -> EmbeddingResult:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = [((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(self.dimension)]
        return EmbeddingResult(vector, 0)
