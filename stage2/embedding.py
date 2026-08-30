"""CLOVA query-embedding adapter used by the fixture and future stores."""

from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from stage2.json_fixture import EmbeddingUnavailable

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for direct adapter imports
    load_dotenv = None


class ClovaQueryEmbedding:
    def __init__(self, *, timeout: float = 30.0):
        if load_dotenv is not None:
            load_dotenv()
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
        except HTTPError as error:
            reset = error.headers.get("x-ratelimit-reset-requests") or error.headers.get("Retry-After")
            match = re.search(r"\d+(?:\.\d+)?", str(reset or ""))
            retry_after = float(match.group(0)) if match else None
            raise EmbeddingUnavailable(
                f"CLOVA embedding request failed: HTTP {error.code}",
                retry_after=retry_after,
                status_code=error.code,
                retryable=error.code == 429,
            ) from error
        except (URLError, TimeoutError) as error:
            reason = getattr(error, "reason", None) or str(error)
            raise EmbeddingUnavailable(
                f"CLOVA embedding request failed: {type(error).__name__}: {reason}",
                retryable=True,
            ) from error
        vector = payload.get("result", {}).get("embedding", [])
        if payload.get("status", {}).get("code") != "20000" or not isinstance(vector, list) or len(vector) != 1024:
            raise EmbeddingUnavailable("CLOVA embedding response is invalid")
        return [float(value) for value in vector]


__all__ = ["ClovaQueryEmbedding"]
