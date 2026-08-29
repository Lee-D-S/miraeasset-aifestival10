from __future__ import annotations

import json
import re
from time import monotonic
from typing import Any, Callable
from urllib.request import Request, urlopen

from agentic_rag.infrastructure.retry import retry_call
from agentic_rag.llm.cache import ResponseCache
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE, ModelProfile


def _text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in value)
    return str(value or "")


def _strip_json_fence(value: str) -> str:
    return re.sub(r"^\s*```(?:json)?\s*|\s*```\s*$", "", value, flags=re.IGNORECASE | re.DOTALL).strip()


def _validate_schema(value: Any, schema: dict[str, Any]) -> None:
    if schema.get("type") == "object":
        if not isinstance(value, dict):
            raise ValueError("structured output must be an object")
        for field in schema.get("required", []):
            if field not in value:
                raise ValueError(f"missing required field: {field}")
        for field, rule in schema.get("properties", {}).items():
            if field not in value:
                continue
            actual = value[field]
            expected = rule.get("type")
            if expected == "string" and not isinstance(actual, str):
                raise ValueError(f"field {field} must be a string")
            if expected == "number" and (not isinstance(actual, (int, float)) or isinstance(actual, bool)):
                raise ValueError(f"field {field} must be a number")
            if expected == "object" and not isinstance(actual, dict):
                raise ValueError(f"field {field} must be an object")
            if expected == "array" and not isinstance(actual, list):
                raise ValueError(f"field {field} must be an array")
            if rule.get("enum") and actual not in rule["enum"]:
                raise ValueError(f"field {field} has an invalid enum value")


class ChatClovaXClient:
    ENDPOINT = "/v3/chat-completions"

    def __init__(self, *, transport: Callable[[dict[str, Any], ModelProfile], dict[str, Any]] | None = None, api_host: str | None = None, api_key: str | None = None, cache: ResponseCache | None = None) -> None:
        self.transport = transport
        self.api_host = api_host
        self.api_key = api_key
        self.cache = cache or ResponseCache()
        self.calls = 0
        self.cache_hits = 0
        self.metrics: list[dict[str, Any]] = []

    def _request(self, payload: dict[str, Any], profile: ModelProfile) -> dict[str, Any]:
        self.calls += 1
        started = monotonic()
        def operation() -> dict[str, Any]:
            if self.transport is not None:
                return self.transport(payload, profile)
            if not self.api_host or not self.api_key:
                raise RuntimeError("CLOVA_API_KEY is not configured")
            request = Request(f"https://{self.api_host}{self.ENDPOINT}/{profile.model}", data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {self.api_key}"}, method="POST")
            with urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))

        result = retry_call(operation, attempts=2, base_delay=0.1)
        if result.get("status", {}).get("code") not in (None, "20000"):
            raise RuntimeError(f"Chat Completions request failed: {result.get('status')}")
        self.metrics.append({"model": profile.model, "latency_ms": round((monotonic() - started) * 1000, 2), "usage": result.get("result", {}).get("usage", result.get("usage", {}))})
        return result.get("result", result)

    @staticmethod
    def _content(result: dict[str, Any]) -> str:
        message = result.get("message", {})
        if not message:
            messages = result.get("messages", [])
            message = messages[-1] if messages else {}
        return _text(message.get("content", ""))

    def generate_text(self, messages: list[dict[str, Any]], *, profile: ModelProfile = DEFAULT_PROFILE) -> str:
        key = self.cache.key(messages, profile)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return str(cached)
        payload = {"messages": messages, "maxCompletionTokens": profile.max_tokens, "temperature": profile.temperature, "seed": 0, "repetitionPenalty": 1.1}
        result = self._request(payload, profile)
        answer = self._content(result)
        if not answer.strip():
            raise ValueError("Chat Completions returned an empty answer")
        self.cache.put(key, answer)
        return answer

    def generate_json(self, messages: list[dict[str, Any]], *, schema: dict[str, Any], profile: ModelProfile = DEFAULT_PROFILE) -> dict[str, Any]:
        request_messages = list(messages)
        if not profile.structured_outputs:
            request_messages.insert(0, {"role": "system", "content": "JSON 객체 하나만 출력하세요. Markdown code fence를 사용하지 마세요."})
        key = self.cache.key(request_messages, profile, schema)
        cached = self.cache.get(key)
        if cached is not None:
            self.cache_hits += 1
            return dict(cached)
        payload = {"messages": request_messages, "maxCompletionTokens": profile.max_tokens, "temperature": profile.temperature, "seed": 0, "repetitionPenalty": 1.1}
        if profile.structured_outputs:
            payload["responseFormat"] = {"type": "json", "schema": schema}
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                result = self._request(payload, profile)
                value = json.loads(_strip_json_fence(self._content(result)))
                _validate_schema(value, schema)
                self.cache.put(key, value)
                return value
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    request_messages.append({"role": "user", "content": "이전 출력은 schema를 만족하지 않았습니다. 유효한 JSON 객체만 다시 출력하세요."})
                    payload["messages"] = request_messages
        raise ValueError("Chat Completions structured output validation failed") from last_error
