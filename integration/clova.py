"""Minimal CLOVA Chat Completions adapter for answer and semantic validation."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any
from urllib.request import Request, urlopen


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

    def __init__(self, *, host: str | None = None, api_key: str | None = None, model: str | None = None, timeout: float = 120.0):
        self.host = (host or os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com")).strip()
        self.api_key = (api_key or os.getenv("CLOVA_API_KEY", "")).strip()
        self.model = model or os.getenv("CLOVA_CHAT_MODEL", "HCX-DASH-002")
        self.timeout = timeout

    def _request(self, messages: list[dict[str, Any]], *, max_tokens: int = 1024) -> str:
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
        with urlopen(request, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if payload.get("status", {}).get("code") not in (None, "20000"):
            raise RuntimeError(f"CLOVA chat request failed: {payload.get('status')}")
        answer = _content(payload.get("result", payload))
        if not answer.strip():
            raise ValueError("CLOVA chat returned an empty answer")
        return answer

    def generate_text(self, messages: list[dict[str, Any]], **_: Any) -> str:
        return self._request(messages)

    def generate_json(self, messages: list[dict[str, Any]], *, schema: Mapping[str, Any], **_: Any) -> dict[str, Any]:
        prompt = list(messages) + [{
            "role": "user",
            "content": "Return one JSON object only. Follow this schema exactly:\n" + json.dumps(schema, ensure_ascii=False),
        }]
        value = re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", self._request(prompt), flags=re.IGNORECASE | re.DOTALL).strip()
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("CLOVA semantic response must be a JSON object")
        return parsed


__all__ = ["ClovaChatClient"]
