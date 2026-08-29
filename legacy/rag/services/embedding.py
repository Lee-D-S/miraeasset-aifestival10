from rag.services.clova_client import ClovaClient, EmbeddingResult


class EmbeddingService:
    """Embedding v2 boundary; vectors will be persisted in a vector DB later."""

    def __init__(self, clova_client: ClovaClient | None = None) -> None:
        self.clova_client = clova_client or ClovaClient()

    def embed(self, text: str) -> EmbeddingResult:
        return self.clova_client.embed_text(text)
