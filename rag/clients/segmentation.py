from dataclasses import dataclass

from rag.clients.base import ClovaApiClient


@dataclass(frozen=True)
class SegmentationResult:
    paragraphs: list[str]
    spans: list[list[int]]
    input_tokens: int


class SegmentationClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def segment_text(self, text: str, *, alpha: float = -100, seg_cnt: int = -1, post_process: bool = True, post_process_max_size: int = 1000, post_process_min_size: int = 300) -> SegmentationResult:
        if not text.strip():
            return SegmentationResult([], [], 0)
        result = self.api.result_or_raise(self.api.post("/v1/api-tools/segmentation", {
            "text": text, "alpha": alpha, "segCnt": seg_cnt,
            "postProcess": post_process, "postProcessMaxSize": post_process_max_size,
            "postProcessMinSize": post_process_min_size,
        }), "segmentation")
        topic_segments = result.get("topicSeg", [])
        paragraphs = [" ".join(segment).strip() for segment in topic_segments if segment]
        return SegmentationResult(paragraphs, result.get("span", []), result.get("inputTokens", 0))

