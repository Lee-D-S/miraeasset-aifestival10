from __future__ import annotations

import os
from typing import Any


class ProviderConfigurationError(RuntimeError):
    classification = "api_configuration"


class DependencyConfigurationError(RuntimeError):
    classification = "dependency_issue"


def build_stage2_chat_model() -> Any:
    """Create the real Stage2 ChatClovaX provider lazily at runtime."""

    try:
        from langchain_naver import ChatClovaX
    except ImportError as error:
        raise DependencyConfigurationError(
            "langchain_naver가 설치되어 있지 않아 실제 Stage2 LLM을 초기화할 수 없습니다."
        ) from error

    key_names = ("CLOVASTUDIO_API_KEY", "CLOVA_API_KEY")
    if not any(os.getenv(name, "").strip() for name in key_names):
        raise ProviderConfigurationError(
            "CLOVASTUDIO_API_KEY 또는 CLOVA_API_KEY가 설정되지 않았습니다."
        )
    try:
        return ChatClovaX(model="HCX-005", temperature=0.1)
    except Exception as error:  # noqa: BLE001 - provider initialization boundary
        raise ProviderConfigurationError(
            f"ChatClovaX 초기화에 실패했습니다: {type(error).__name__}"
        ) from error
