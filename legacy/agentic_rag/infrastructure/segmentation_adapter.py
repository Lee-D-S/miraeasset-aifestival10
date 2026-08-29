import json
from typing import Protocol
from urllib.request import Request, urlopen
from agentic_rag.infrastructure.retry import retry_call


class SegmentationPort(Protocol):
    def segment(self, text: str) -> list[str]: ...


class SegmentationAdapter:
    def __init__(self, segmenter):
        self.segmenter = segmenter

    def segment(self, text: str) -> list[str]:
        result = self.segmenter.segment_text(text) if hasattr(self.segmenter, "segment_text") else self.segmenter(text)
        return list(getattr(result, "paragraphs", result))


class ClovaSegmentation:
    def segment(self, text: str) -> list[str]:
        from common.config import settings
        if not settings.clova_api_key:
            raise RuntimeError("CLOVA_API_KEY is required for CLOVA segmentation")
        payload = {"text": text, "alpha": -100, "segCnt": -1, "postProcess": True, "postProcessMaxSize": 1000, "postProcessMinSize": 300}
        request = Request(
            f"https://{settings.clova_api_host}/v1/api-tools/segmentation",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {settings.clova_api_key}"},
            method="POST",
        )
        with retry_call(lambda: urlopen(request, timeout=120)) as response:
            body = json.loads(response.read().decode("utf-8"))
        paragraphs = body.get("result", {}).get("topicSeg", [])
        return [str(item.get("text", item)) for item in paragraphs]
