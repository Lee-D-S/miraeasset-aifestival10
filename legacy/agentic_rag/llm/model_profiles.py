from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    model: str
    max_tokens: int = 512
    temperature: float = 0.1
    structured_outputs: bool = False
    task_id: str | None = None


DEFAULT_PROFILE = ModelProfile(
    model=os.getenv("AGENTIC_LLM_MODEL", "HCX-DASH-002"),
    max_tokens=int(os.getenv("AGENTIC_LLM_MAX_TOKENS", "512")),
    temperature=float(os.getenv("AGENTIC_LLM_TEMPERATURE", "0.1")),
)

QUALITY_PROFILE = ModelProfile(model="HCX-007", max_tokens=1024, temperature=0.1, structured_outputs=True)
