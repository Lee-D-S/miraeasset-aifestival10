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

    def __init__(self, *, host: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float | None = None, max_retries: int | None = None):
        self.host = (host or os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com")).strip()
        self.api_key = (api_key or os.getenv("CLOVA_API_KEY", "")).strip()
        self.model = model or os.getenv("CLOVA_CHAT_MODEL", "HCX-DASH-002")
        self.timeout = timeout if timeout is not None else _env_float("CLOVA_CHAT_TIMEOUT", 60.0)
        self.max_retries = max_retries if max_retries is not None else _env_int("CLOVA_CHAT_MAX_RETRIES", 1)
        self.rate_limit_max_wait = _env_float("CLOVA_RATE_LIMIT_MAX_WAIT", 15.0)

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
        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                break
            except HTTPError as error:
                if error.code != 429 or attempt >= self.max_retries:
                    detail = ""
                    try:
                        body = json.loads(error.read().decode("utf-8"))
                        detail = str(body.get("status", {}).get("code") or body.get("status", {}).get("message") or "")
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        pass
                    suffix = f" ({detail})" if detail else ""
                    raise RuntimeError(f"CLOVA {operation} HTTP {error.code}{suffix}") from error
                reset = error.headers.get("x-ratelimit-reset-requests") or error.headers.get("Retry-After")
                match = re.search(r"\d+(?:\.\d+)?", str(reset or ""))
                requested_wait = float(match.group(0)) if match else 2.0 ** attempt
                time.sleep(min(max(requested_wait, 1.0), self.rate_limit_max_wait))
        if payload.get("status", {}).get("code") not in (None, "20000"):
            raise RuntimeError(f"CLOVA {operation} request failed: {payload.get('status')}")
        answer = _content(payload.get("result", payload))
        if not answer.strip():
            raise ValueError("CLOVA chat returned an empty answer")
        return answer

    def generate_text(self, messages: list[dict[str, Any]], **_: Any) -> str:
        return self._request(messages, operation="answer_generation")

    def generate_json(self, messages: list[dict[str, Any]], *, schema: Mapping[str, Any], **_: Any) -> dict[str, Any]:
        prompt = list(messages) + [{
            "role": "user",
            "content": "Return one JSON object only. Follow this schema exactly:\n" + json.dumps(schema, ensure_ascii=False),
        }]
        value = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", self._request(prompt, operation="semantic_validation"), flags=re.IGNORECASE | re.DOTALL).strip()
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("CLOVA semantic response must be a JSON object")
        return parsed


__all__ = ["ClovaChatClient"]
