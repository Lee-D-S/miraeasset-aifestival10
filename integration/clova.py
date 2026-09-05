"""Minimal CLOVA Chat Completions adapter for answer and semantic validation."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
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


def _content(result: Mapping[str, Any]) -> str:
    message = result.get("message", {})
    if not message:
        messages = result.get("messages", [])
        message = messages[-1] if messages else {}
    value = message.get("content", "") if isinstance(message, Mapping) else ""
    if isinstance(value, list):
        return "".join(str(item.get("text", item)) if isinstance(item, Mapping) else str(item) for item in value)
    return str(value or "")


class ClovaChatClient:
    endpoint = "/v3/chat-completions"
    strict_grounding = True

    def __init__(self, *, host: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float | None = None, max_retries: int | None = None, rate_limiter: ClovaRateLimiter | None = None):
        self.host = (host or os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com")).strip()
        self.api_key = (
            api_key
            or os.getenv("CLOVA_API_KEY", "")
            or os.getenv("CLOVASTUDIO_API_KEY", "")
        ).strip()
        self.model = model or os.getenv("CLOVA_CHAT_MODEL", "HCX-DASH-002")
        self.timeout = timeout if timeout is not None else _env_float("CLOVA_CHAT_TIMEOUT", 60.0)
        self.max_retries = max_retries if max_retries is not None else _env_int("CLOVA_CHAT_MAX_RETRIES", 1)
        # A question has a 300-second budget; wait through a normal provider
        # reset window before surfacing a capacity error to the API boundary.
        self.rate_limit_max_wait = _env_float("CLOVA_RATE_LIMIT_MAX_WAIT", 300.0)
        self.last_rate_limit: dict[str, str] = {}
        self.last_provider_status: dict[str, Any] = {}
        self.rate_limiter = rate_limiter or ClovaRateLimiter(default_qpm=90, default_tpm=80000, min_interval=_env_float("CLOVA_CHAT_MIN_INTERVAL", 0.2))

    def _capture_rate_limit(self, headers: Any) -> None:
        self.last_rate_limit = {
            str(key).lower(): str(value)
            for key, value in headers.items()
            if str(key).lower().startswith("x-ratelimit-")
        }
        self.rate_limiter.observe(self.last_rate_limit)
        self.last_provider_status = {
            "status": "observed",
            "rate_limit_headers": dict(self.last_rate_limit),
            "limiter": self.rate_limiter.snapshot(),
        }

    def _request(self, messages: list[dict[str, Any]], *, max_tokens: int = 1024, operation: str = "chat") -> str:
        if not self.api_key:
            raise RuntimeError("CLOVA_API_KEY is not configured")
        request = Request(
            f"https://{self.host}{self.endpoint}/{self.model}",
            data=json.dumps({
                "messages": messages,
                "maxCompletionTokens": max_tokens,
                "temperature": 0.1,
                "seed": 0,
                "repetitionPenalty": 1.1,
            }, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        estimated_tokens = estimate_tokens(json.dumps(messages, ensure_ascii=False)) + max_tokens
        for attempt in range(self.max_retries + 1):
            try:
                self.rate_limiter.before_call(estimated_tokens)
            except RateLimitBlocked as error:
                self.last_provider_status = {
                    "status": "rate_limited",
                    "source": "local_admission",
                    "operation": operation,
                    "error_type": type(error).__name__,
                    "message": str(error),
                    "retry_after_seconds": round(error.retry_after, 3),
                    "estimated_tokens": estimated_tokens,
                    "rate_limit_headers": dict(self.last_rate_limit),
                    "limiter": self.rate_limiter.snapshot(),
                }
                if error.retry_after and error.retry_after <= self.rate_limit_max_wait and attempt < self.max_retries:
                    remaining = remaining_question_seconds()
                    if remaining is not None and error.retry_after > remaining:
                        raise RateLimitBlocked("질의 전체 rate-limit 대기 한도를 초과했습니다.", error.retry_after) from error
                    time.sleep(error.retry_after)
                    continue
                raise
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    self._capture_rate_limit(response.headers)
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                self._capture_rate_limit(error.headers)
                if error.code != 429 or attempt >= self.max_retries:
                    detail = ""
                    try:
                        body = json.loads(error.read().decode("utf-8"))
                        detail = str(body.get("status", {}).get("code") or body.get("status", {}).get("message") or "")
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        pass
                    suffix = f" ({detail})" if detail else ""
                    rate_suffix = f" [rate_limit={self.last_rate_limit}]" if self.last_rate_limit else ""
                    self.last_provider_status = {
                        "status": "rate_limited" if error.code == 429 else "provider_error",
                        "source": "provider_http",
                        "operation": operation,
                        "error_type": type(error).__name__,
                        "status_code": error.code,
                        "message": str(error),
                        "rate_limit_headers": dict(self.last_rate_limit),
                        "limiter": self.rate_limiter.snapshot(),
                    }
                    raise RuntimeError(f"CLOVA {operation} HTTP {error.code}{suffix}{rate_suffix}") from error
                reset = error.headers.get("x-ratelimit-reset-requests") or error.headers.get("Retry-After")
                match = re.search(r"\d+(?:\.\d+)?", str(reset or ""))
                requested_wait = float(match.group(0)) if match else 2.0 ** attempt
                wait = min(max(requested_wait, 1.0), self.rate_limit_max_wait)
                remaining = remaining_question_seconds()
                if remaining is not None and wait > remaining:
                    raise RateLimitBlocked("질의 전체 rate-limit 대기 한도를 초과했습니다.", wait) from error
                time.sleep(wait)
        if payload.get("status", {}).get("code") not in (None, "20000"):
            raise RuntimeError(f"CLOVA {operation} request failed: {payload.get('status')}")
        answer = _content(payload.get("result", payload))
        if not answer.strip():
            raise ValueError("CLOVA chat returned an empty answer")
        return answer

    def generate_text(self, messages: list[dict[str, Any]], **_: Any) -> str:
        return self._request(
            messages,
            max_tokens=_env_int("CLOVA_ANSWER_MAX_TOKENS", 256),
            operation="answer_generation",
        )

    def generate_json(self, messages: list[dict[str, Any]], *, schema: Mapping[str, Any], operation: str = "semantic_validation", **_: Any) -> dict[str, Any]:
        prompt = list(messages) + [{
            "role": "user",
            "content": "Return one JSON object only. Follow this schema exactly:\n" + json.dumps(schema, ensure_ascii=False),
        }]
        value = re.sub(
            r"^\s*```(?:json)?\s*|\s*```\s*$",
            "",
            self._request(
                prompt,
                max_tokens=_env_int("CLOVA_SEMANTIC_MAX_TOKENS", 128),
                operation=operation,
            ),
            flags=re.IGNORECASE | re.DOTALL,
        ).strip()
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("CLOVA semantic response must be a JSON object")
        return parsed


__all__ = ["ClovaChatClient"]
