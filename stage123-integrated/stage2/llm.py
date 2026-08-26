from __future__ import annotations

from typing import Any

from app.clova_config import DEFAULT_CLOVA_CHAT_MODEL, get_clova_api_key


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

    api_key = get_clova_api_key()
    if not api_key:
        raise ProviderConfigurationError(
            "CLOVA_API_KEY가 설정되지 않았습니다."
        )
    try:
        return ChatClovaX(model=DEFAULT_CLOVA_CHAT_MODEL, temperature=0.1, api_key=api_key)
    except Exception as error:  # noqa: BLE001 - provider initialization boundary
        raise ProviderConfigurationError(
            f"ChatClovaX 초기화에 실패했습니다: {type(error).__name__}"
        ) from error
