"""Backward-compatible facade for the split CLOVA API clients."""

from rag.clients.embedding import EmbeddingClient, EmbeddingResult
from rag.clients.rag_reasoning import RagReasoningClient
from rag.clients.reranker import RerankerClient, RerankerResult
from rag.clients.segmentation import SegmentationClient, SegmentationResult


class ClovaClient:
    """Temporary facade; new code should depend on API-specific clients."""

    def __init__(self) -> None:
        self.segmentation = SegmentationClient()
        self.embedding = EmbeddingClient()
        self.reranker = RerankerClient()
        self.rag_reasoning = RagReasoningClient()

    def segment_text(self, *args, **kwargs) -> SegmentationResult:
        return self.segmentation.segment_text(*args, **kwargs)

    def embed_text(self, *args, **kwargs) -> EmbeddingResult:
        return self.embedding.embed_text(*args, **kwargs)

    def rerank_documents(self, *args, **kwargs) -> RerankerResult:
        return self.reranker.rerank_documents(*args, **kwargs)

    def generate_answer(self, *args, **kwargs):
        return self.rag_reasoning.generate(*args, **kwargs)

