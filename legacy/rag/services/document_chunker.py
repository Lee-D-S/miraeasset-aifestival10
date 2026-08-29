from rag.services.clova_client import ClovaClient, SegmentationResult


class DocumentChunker:
    """Document chunking boundary; later stages can pass chunks to Embedding v2."""

    def __init__(self, clova_client: ClovaClient | None = None) -> None:
        self.clova_client = clova_client or ClovaClient()

    def chunk(self, text: str) -> SegmentationResult:
        return self.clova_client.segment_text(text)
