from __future__ import annotations

import os

from dotenv import load_dotenv


load_dotenv()


def get_clova_api_key() -> str:
    """Return the unified CLOVA Studio key, with legacy-name fallback."""

    return os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()


__all__ = ["get_clova_api_key"]
