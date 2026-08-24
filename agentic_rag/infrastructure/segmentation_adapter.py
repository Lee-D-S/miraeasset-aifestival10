from typing import Protocol


class SegmentationPort(Protocol):
    def segment(self, text: str) -> list[str]: ...


class SegmentationAdapter:
    def __init__(self, segmenter):
        self.segmenter = segmenter

    def segment(self, text: str) -> list[str]:
        result = self.segmenter.segment_text(text) if hasattr(self.segmenter, "segment_text") else self.segmenter(text)
        return list(getattr(result, "paragraphs", result))

