"""CLOVA query-embedding adapters used by the fixture and local stores."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from langchain_core.embeddings import Embeddings

from stage2.json_fixture import EmbeddingUnavailable
from integration.rate_limit import (
    ClovaRateLimiter,
    RateLimitBlocked,
    estimate_tokens,
    remaining_question_seconds,
)

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional for direct adapter imports
    load_dotenv = None


class ClovaQueryEmbedding:
    def __init__(self, *, timeout: float | None = None, rate_limiter: ClovaRateLimiter | None = None):
        if load_dotenv is not None:
            load_dotenv()
        try:
            configured = float(os.getenv("CLOVA_EMBEDDING_TIMEOUT", "30"))
        except ValueError:
            configured = 30.0
        self.timeout = timeout if timeout is not None else max(configured, 0.1)
        self.last_rate_limit: dict[str, str] = {}
        self.rate_limiter = rate_limiter or ClovaRateLimiter(default_qpm=60, default_tpm=40000)

    def _capture_rate_limit(self, headers) -> None:
        self.last_rate_limit = {
            str(key).lower(): str(value)
            for key, value in headers.items()
            if str(key).lower().startswith("x-ratelimit-")
        }
        self.rate_limiter.observe(self.last_rate_limit)

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
            max_retries = max(int(os.getenv("CLOVA_EMBEDDING_MAX_RETRIES", "1")), 0)
        except ValueError:
            max_retries = 1
        try:
            max_wait = max(float(os.getenv("CLOVA_RATE_LIMIT_MAX_WAIT", "60")), 0.1)
        except ValueError:
            max_wait = 60.0
        for attempt in range(max_retries + 1):
            try:
                self.rate_limiter.before_call(estimate_tokens(text))
                with urlopen(request, timeout=self.timeout) as response:
                    self._capture_rate_limit(response.headers)
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except RateLimitBlocked as error:
                if error.retry_after <= 0 or error.retry_after > max_wait or attempt >= max_retries:
                    raise EmbeddingUnavailable(
                        f"CLOVA embedding rate limit blocked: {error}",
                        retry_after=error.retry_after,
                        retryable=True,
                    ) from error
                remaining = remaining_question_seconds()
                if remaining is not None and error.retry_after > remaining:
                    raise EmbeddingUnavailable(
                        "질의 전체 rate-limit 대기 한도를 초과했습니다.",
                        retry_after=error.retry_after,
                        retryable=True,
                    ) from error
                time.sleep(error.retry_after)
            except HTTPError as error:
                self._capture_rate_limit(error.headers)
                reset = error.headers.get("x-ratelimit-reset-requests") or error.headers.get("Retry-After")
                match = re.search(r"\d+(?:\.\d+)?", str(reset or ""))
                retry_after = float(match.group(0)) if match else 2.0 ** attempt
                if error.code == 429 and attempt < max_retries and retry_after <= max_wait:
                    remaining = remaining_question_seconds()
                    if remaining is not None and retry_after > remaining:
                        raise EmbeddingUnavailable(
                            "질의 전체 rate-limit 대기 한도를 초과했습니다.",
                            retry_after=retry_after,
                            status_code=error.code,
                            retryable=True,
                        ) from error
                    time.sleep(min(max(retry_after, 1.0), max_wait))
                    continue
                raise EmbeddingUnavailable(
                    f"CLOVA embedding request failed: HTTP {error.code}; rate_limit={self.last_rate_limit}",
                    retry_after=retry_after if error.code == 429 else None,
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


class ClovaEmbeddings(Embeddings):
    """LangChain ``Embeddings`` adapter so vector stores (Chroma) own the
    embed-then-rank pipeline instead of Stage2 computing cosine similarity by
    hand. Wraps the same CLOVA HTTP call as :class:`ClovaQueryEmbedding`.
    """

    def __init__(self, *, timeout: float = 30.0, rate_limiter: ClovaRateLimiter | None = None):
        self._embed_one = ClovaQueryEmbedding(timeout=timeout, rate_limiter=rate_limiter)

    def embed_query(self, text: str) -> list[float]:
        return list(self._embed_one(text))

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [list(self._embed_one(text)) for text in texts]


__all__ = ["ClovaEmbeddings", "ClovaQueryEmbedding"]
