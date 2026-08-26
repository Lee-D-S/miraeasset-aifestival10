from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .stage2_repository import RetrievalError


class ClovaQueryEmbedding:
    """Minimal CLOVA Studio embedding client for the local JSON backend."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def __call__(self, text: str) -> list[float]:
        api_key = os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()
        if not api_key:
            raise RetrievalError("api_configuration", "CLOVA embedding API key is not configured")
        host = os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com").strip()
        request = Request(
            f"https://{host}/v1/api-tools/embedding/v2",
            data=json.dumps({"text": text}, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RetrievalError("api_configuration", f"CLOVA embedding HTTP {error.code}") from error
        except URLError as error:
            raise RetrievalError("api_configuration", "CLOVA embedding request failed") from error
        status = payload.get("status", {})
        if status.get("code") != "20000":
            raise RetrievalError("api_configuration", "CLOVA embedding returned a non-success status")
        vector = payload.get("result", {}).get("embedding", [])
        if not isinstance(vector, list) or len(vector) != 1024:
            raise RetrievalError("schema_issue", f"unexpected query embedding dimension: {len(vector) if isinstance(vector, list) else 0}")
        return [float(value) for value in vector]
