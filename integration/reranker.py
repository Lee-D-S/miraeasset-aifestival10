"""CLOVA Studio Reranker adapter for the production retrieval path."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from integration.rate_limit import (
    ClovaRateLimiter,
    RateLimitBlocked,
    estimate_tokens,
    remaining_question_seconds,
)


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 0.1)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 0)
    except ValueError:
        return default


def _document_id(document: Mapping[str, Any]) -> str:
    return str(
        document.get("id")
        or document.get("chunk_id")
        or document.get("document_id")
        or document.get("doc_id")
        or ""
    ).strip()


def _document_text(document: Mapping[str, Any]) -> str:
    return str(
        document.get("text")
        or document.get("page_content")
        or document.get("text_content")
        or ""
    ).strip()


class ClovaRerankerClient:
    """Call the CLOVA Reranker API and restore the full local documents."""

    endpoint = "/v1/api-tools/reranker"

    def __init__(
        self,
        *,
        host: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        max_tokens: int | None = None,
        rate_limiter: ClovaRateLimiter | None = None,
    ) -> None:
        self.host = (
            host or os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com")
        ).strip()
        self.api_key = (
            api_key
            or os.getenv("CLOVA_API_KEY", "")
            or os.getenv("CLOVASTUDIO_API_KEY", "")
        ).strip()
        self.timeout = timeout if timeout is not None else _env_float(
            "CLOVA_RERANKER_TIMEOUT", 60.0
        )
        self.max_retries = max_retries if max_retries is not None else _env_int(
            "CLOVA_RERANKER_MAX_RETRIES", 1
        )
        self.max_tokens = max_tokens if max_tokens is not None else _env_int(
            "CLOVA_RERANKER_MAX_TOKENS", 1024
        )
        self.rate_limit_max_wait = _env_float("CLOVA_RATE_LIMIT_MAX_WAIT", 60.0)
        self.last_rate_limit: dict[str, str] = {}
        self.last_provider_status: dict[str, Any] = {}
        self.suggested_queries: list[str] = []
        self.rate_limiter = rate_limiter or ClovaRateLimiter(
            default_qpm=_env_int("CLOVA_RATE_LIMIT_QPM", 60),
            default_tpm=_env_int("CLOVA_RATE_LIMIT_TPM", 40000),
            min_interval=max(_env_float("CLOVA_CHAT_MIN_INTERVAL", 0.2), 0.2),
        )

    def _capture_rate_limit(self, headers: Any) -> None:
        self.last_rate_limit = {
            str(key).lower(): str(value)
            for key, value in headers.items()
            if str(key).lower().startswith("x-ratelimit-")
        }
        self.rate_limiter.observe(self.last_rate_limit)
        self.last_provider_status = {
            "status": "observed",
            "operation": "reranker",
            "rate_limit_headers": dict(self.last_rate_limit),
            "limiter": self.rate_limiter.snapshot(),
        }

    def _request(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if not self.api_key:
            raise RuntimeError("CLOVA_API_KEY is not configured")
        encoded = json.dumps(payload, ensure_ascii=False)
        request = Request(
            f"https://{self.host}{self.endpoint}",
            data=encoded.encode("utf-8"),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        estimated_tokens = estimate_tokens(encoded) + self.max_tokens
        for attempt in range(self.max_retries + 1):
            try:
                self.rate_limiter.before_call(estimated_tokens)
            except RateLimitBlocked as error:
                self.last_provider_status = {
                    "status": "rate_limited",
                    "source": "local_admission",
                    "operation": "reranker",
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "retry_after_seconds": round(error.retry_after, 3),
                    "estimated_tokens": estimated_tokens,
                    "rate_limit_headers": dict(self.last_rate_limit),
                    "limiter": self.rate_limiter.snapshot(),
                }
                if (
                    error.retry_after
                    and error.retry_after <= self.rate_limit_max_wait
                    and attempt < self.max_retries
                ):
                    remaining = remaining_question_seconds()
                    if remaining is not None and error.retry_after > remaining:
                        raise RateLimitBlocked(
                            "질의 전체 rate-limit 대기 한도를 초과했습니다.",
                            error.retry_after,
                        ) from error
                    time.sleep(error.retry_after)
                    continue
                raise
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    self._capture_rate_limit(response.headers)
                    raw = response.read().decode("utf-8")
                parsed = json.loads(raw)
                break
            except HTTPError as error:
                self._capture_rate_limit(error.headers)
                if error.code == 429 and attempt < self.max_retries:
                    reset = error.headers.get("x-ratelimit-reset-requests") or error.headers.get("Retry-After")
                    match = re.search(r"\d+(?:\.\d+)?", str(reset or ""))
                    wait = min(
                        max(float(match.group(0)) if match else 2.0**attempt, 1.0),
                        self.rate_limit_max_wait,
                    )
                    remaining = remaining_question_seconds()
                    if remaining is not None and wait > remaining:
                        raise RateLimitBlocked(
                            "질의 전체 rate-limit 대기 한도를 초과했습니다.", wait
                        ) from error
                    time.sleep(wait)
                    continue
                self.last_provider_status = {
                    "status": "rate_limited" if error.code == 429 else "provider_error",
                    "source": "provider_http",
                    "operation": "reranker",
                    "error_type": type(error).__name__,
                    "status_code": error.code,
                    "rate_limit_headers": dict(self.last_rate_limit),
                    "limiter": self.rate_limiter.snapshot(),
                }
                raise RuntimeError(f"CLOVA reranker HTTP {error.code}") from error
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                self.last_provider_status = {
                    "status": "provider_error",
                    "source": "provider_response",
                    "operation": "reranker",
                    "error_type": type(error).__name__,
                    "rate_limit_headers": dict(self.last_rate_limit),
                    "limiter": self.rate_limiter.snapshot(),
                }
                raise RuntimeError("CLOVA reranker response could not be parsed") from error
        if not isinstance(parsed, Mapping):
            raise ValueError("CLOVA reranker response must be an object")
        status = parsed.get("status")
        if isinstance(status, Mapping) and status.get("code") not in (None, "20000"):
            self.last_provider_status = {
                "status": "provider_error",
                "source": "provider_response",
                "operation": "reranker",
                "provider_code": str(status.get("code")),
                "limiter": self.rate_limiter.snapshot(),
            }
            raise RuntimeError("CLOVA reranker request failed")
        return parsed

    def rerank(
        self,
        query: str,
        documents: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[Mapping[str, Any]]:
        if limit <= 0:
            return []
        original_by_id: dict[str, Mapping[str, Any]] = {}
        request_documents: list[dict[str, str]] = []
        for document in documents:
            identifier = _document_id(document)
            text = _document_text(document)
            if not identifier or not text or identifier in original_by_id:
                continue
            original_by_id[identifier] = document
            request_documents.append({"id": identifier, "doc": text})
        if not request_documents:
            return []

        payload = {
            "documents": request_documents,
            "query": str(query).strip(),
            "maxTokens": self.max_tokens,
        }
        response = self._request(payload)
        result = response.get("result", response)
        if not isinstance(result, Mapping):
            raise ValueError("CLOVA reranker result must be an object")
        raw_suggestions = result.get("suggestedQueries", [])
        self.suggested_queries = (
            [str(item) for item in raw_suggestions if str(item).strip()]
            if isinstance(raw_suggestions, list)
            else []
        )
        cited = result.get("citedDocuments")
        if not isinstance(cited, list):
            raise ValueError("CLOVA reranker citedDocuments must be an array")
        ranked: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for item in cited:
            identifier = (
                _document_id(item)
                if isinstance(item, Mapping)
                else str(item).strip()
            )
            if not identifier or identifier in seen:
                continue
            original = original_by_id.get(identifier)
            if original is None:
                continue
            seen.add(identifier)
            ranked.append(dict(original))
            if len(ranked) >= limit:
                break
        if not ranked:
            raise ValueError("CLOVA reranker returned no known cited documents")
        return ranked


__all__ = ["ClovaRerankerClient"]
