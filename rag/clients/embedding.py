from dataclasses import dataclass

from rag.clients.base import ClovaApiClient


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    input_tokens: int


class EmbeddingClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def embed_text(self, text: str) -> EmbeddingResult:
        if not text.strip():
            return EmbeddingResult([], 0)
        result = self.api.result_or_raise(self.api.post("/v1/api-tools/embedding/v2", {"text": text}), "embedding")
        vector = result.get("embedding", [])
        if len(vector) != 1024:
            raise RuntimeError(f"Unexpected embedding dimension: {len(vector)}")
        return EmbeddingResult(vector, result.get("inputTokens", 0))

