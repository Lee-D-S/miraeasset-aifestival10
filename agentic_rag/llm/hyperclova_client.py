from __future__ import annotations

import json
from urllib.request import Request, urlopen
from agentic_rag.infrastructure.retry import retry_call
from typing import Any

class HyperClovaClient:
    """Thin optional adapter; generation is isolated from routing and retrieval."""

    def __init__(self, client: Any | None = None) -> None:
        self.client = client

    def _generate(self, prompt: str) -> dict[str, Any]:
        if self.client is not None:
            return self.client.generate([{"role": "user", "content": prompt}], [])
        from common.config import settings
        if not settings.clova_api_key:
            raise RuntimeError("CLOVA_API_KEY is not configured")
        payload = {"messages": [{"role": "user", "content": prompt}], "tools": [], "toolChoice": "none", "topP": 0.8, "topK": 0, "maxTokens": 1024, "temperature": 0.2, "repetitionPenalty": 1.1, "stop": [], "seed": 0, "includeAiFilters": True}
        request = Request(
            f"https://{settings.clova_api_host}/v1/api-tools/rag-reasoning",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {settings.clova_api_key}"},
            method="POST",
        )
        with retry_call(lambda: urlopen(request, timeout=120)) as response:
            body = json.loads(response.read().decode("utf-8"))
        if body.get("status", {}).get("code") != "20000":
            raise RuntimeError(f"HyperCLOVA request failed: {body.get('status')}")
        return body.get("result", {})

    def generate_json(self, prompt: str) -> dict[str, Any]:
        result = self._generate(prompt)
        text = result.get("messages", [{}])[-1].get("content", "") if result else ""
        if isinstance(text, list):
            text = "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in text)
        try:
            return json.loads(str(text).replace("```json", "").replace("```", "").strip())
        except json.JSONDecodeError as exc:
            raise ValueError("HyperCLOVA structured output is not valid JSON") from exc

    def generate_answer(self, prompt: str) -> str:
        result = self._generate(prompt)
        message = result.get("messages", [{}])[-1] if result else {}
        content = message.get("content", "") if isinstance(message, dict) else str(message)
        return str(content)
