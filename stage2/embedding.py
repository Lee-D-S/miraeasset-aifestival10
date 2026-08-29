"""CLOVA query-embedding adapter used by the fixture and future stores."""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stage2.json_fixture import EmbeddingUnavailable


class ClovaQueryEmbedding:
    def __init__(self, *, timeout: float = 30.0):
        self.timeout = timeout

    def __call__(self, text: str) -> list[float]:
        api_key = os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()
        if not api_key:
            raise EmbeddingUnavailable("CLOVA embedding API key is not configured")
        host = os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com").strip()
        request = Request(
            f"https://{host}/v1/api-tools/embedding/v2",
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {api_key}"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as error:
            raise EmbeddingUnavailable(f"CLOVA embedding request failed: {type(error).__name__}") from error
        vector = payload.get("result", {}).get("embedding", [])
        if payload.get("status", {}).get("code") != "20000" or not isinstance(vector, list) or len(vector) != 1024:
            raise EmbeddingUnavailable("CLOVA embedding response is invalid")
        return [float(value) for value in vector]


__all__ = ["ClovaQueryEmbedding"]
