from __future__ import annotations

from typing import Any, Protocol

from agentic_rag.llm.model_profiles import ModelProfile


class ChatModelPort(Protocol):
    def generate_text(self, messages: list[dict[str, Any]], *, profile: ModelProfile) -> str: ...

    def generate_json(self, messages: list[dict[str, Any]], *, schema: dict[str, Any], profile: ModelProfile) -> dict[str, Any]: ...
