from __future__ import annotations

from typing import Literal


ExecutionMode = Literal["auto", "langgraph", "stdlib"]
EXECUTION_MODES = frozenset({"auto", "langgraph", "stdlib"})


def langgraph_available() -> bool:
    """Return whether the optional LangGraph runtime can be imported."""

    try:
        import langgraph  # noqa: F401 - optional capability check
    except ImportError:
        return False
    return True


def resolve_execution_mode(mode: str = "auto") -> ExecutionMode:
    """Resolve the requested mode without importing LangGraph at module load."""

    normalized = str(mode or "auto").strip().lower()
    if normalized not in EXECUTION_MODES:
        raise ValueError(f"지원하지 않는 Reasoner 실행 모드입니다: {mode}")
    if normalized == "auto":
        return "langgraph" if langgraph_available() else "stdlib"
    if normalized == "langgraph" and not langgraph_available():
        raise RuntimeError("LangGraph 실행 모드를 사용하려면 langgraph 패키지가 필요합니다.")
    return normalized  # type: ignore[return-value]


__all__ = ["EXECUTION_MODES", "ExecutionMode", "langgraph_available", "resolve_execution_mode"]
