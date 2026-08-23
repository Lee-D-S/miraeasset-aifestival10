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

    def segment_text(self, text: str, *, alpha: float = -100, seg_cnt: int = -1, post_process: bool = True, post_process_max_size: int = 1000, post_process_min_size: int = 300, max_input_chars: int = 30_000) -> SegmentationResult:
        if not text.strip():
            return SegmentationResult([], [], 0)
        if len(text) > max_input_chars:
            paragraphs: list[str] = []
            input_tokens = 0
            for part in self._split_text(text, max_input_chars):
                result = self._segment_single(part, alpha=alpha, seg_cnt=seg_cnt, post_process=post_process, post_process_max_size=post_process_max_size, post_process_min_size=post_process_min_size)
                paragraphs.extend(result.paragraphs)
                input_tokens += result.input_tokens
            return SegmentationResult(paragraphs, [], input_tokens)
        return self._segment_single(text, alpha=alpha, seg_cnt=seg_cnt, post_process=post_process, post_process_max_size=post_process_max_size, post_process_min_size=post_process_min_size)

    def _segment_single(self, text: str, *, alpha: float, seg_cnt: int, post_process: bool, post_process_max_size: int, post_process_min_size: int) -> SegmentationResult:
        result = self.api.result_or_raise(self.api.post("/v1/api-tools/segmentation", {
            "text": text, "alpha": alpha, "segCnt": seg_cnt,
            "postProcess": post_process, "postProcessMaxSize": post_process_max_size,
            "postProcessMinSize": post_process_min_size,
        }), "segmentation")
        topic_segments = result.get("topicSeg", [])
        paragraphs = [" ".join(segment).strip() for segment in topic_segments if segment]
        return SegmentationResult(paragraphs, result.get("span", []), result.get("inputTokens", 0))

    @staticmethod
    def _split_text(text: str, max_chars: int) -> list[str]:
        parts = []
        remaining = text
        while len(remaining) > max_chars:
            boundary = remaining.rfind("\n", 0, max_chars)
            if boundary < max_chars // 2:
                boundary = max_chars
            parts.append(remaining[:boundary].strip())
            remaining = remaining[boundary:].lstrip()
        if remaining.strip():
            parts.append(remaining.strip())
        return parts
