"""Offline deployment-readiness checks for the executable pipeline."""

from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any

import config


logger = logging.getLogger(__name__)

# These are expected when a shared index has fewer vectors/documents than the
# Interpreter corpus. They may be downgraded to warnings only when the operator
# explicitly opts into partial-index execution.
PARTIAL_INDEX_ISSUES = frozenset(
    {
        "Chroma is missing SQLite chunk IDs",
        "Chroma contains chunk IDs absent from SQLite",
        "manifest contains documents absent from Retriever index",
        "Retriever index contains documents absent from manifest",
    }
)


def _api_key() -> str:
    return os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()


def validate_environment(mode: str) -> list[str]:
    """Return configuration problems without making a network request."""

    issues: list[str] = []
    if mode not in config.VALID_RETRIEVER_MODES:
        issues.append(
            "RETRIEVER_MODE must be one of " + ", ".join(config.VALID_RETRIEVER_MODES)
        )
    live_llm = os.getenv("CLOVA_LLM_ENABLED", "false").strip().lower() == "true"
    interpreter_llm = os.getenv("INTERPRETER_USE_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}
    query_planner_llm = os.getenv("QUERY_PLANNER_LLM_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    reranker = os.getenv("CLOVA_RERANKER_ENABLED", "false").strip().lower() == "true"
    provider_enabled = live_llm or interpreter_llm or query_planner_llm or reranker
    if provider_enabled and not _api_key():
        issues.append("CLOVA_API_KEY is not configured")
    if provider_enabled and not os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com").strip():
        issues.append("CLOVA_API_HOST is empty")
    embedding = os.getenv("RETRIEVER_EMBEDDING", config.EMBEDDING).strip().lower()
    if embedding not in config.VALID_RETRIEVER_EMBEDDINGS:
        issues.append(
            "RETRIEVER_EMBEDDING must be one of "
            + ", ".join(config.VALID_RETRIEVER_EMBEDDINGS)
        )
    table = os.getenv("RETRIEVER_SQL_TABLE", config.SQLITE_TABLE).strip()
    if table not in config.VALID_RETRIEVER_SQL_TABLES:
        issues.append(
            "RETRIEVER_SQL_TABLE must be one of "
            + ", ".join(config.VALID_RETRIEVER_SQL_TABLES)
        )
    return issues


def raise_if_invalid(issues: list[str], *, tolerate: set[str] | frozenset[str] = frozenset()) -> None:
    """Raise on structural issues and log explicitly tolerated warnings."""

    tolerated = sorted(set(issues) & set(tolerate))
    fatal = [issue for issue in issues if issue not in tolerate]
    for issue in tolerated:
        logger.warning("tolerating partial-index issue: %s", issue)
    if fatal:
        raise RuntimeError("deployment readiness failed: " + "; ".join(fatal))


def validate_embedding_dimension(vectorstore: Any, expected: int = 1024) -> list[str]:
    """Validate one persisted vector without making a provider request."""

    declared_dimension = getattr(vectorstore, "embedding_dimension", None)
    if declared_dimension is not None:
        try:
            dimension = int(declared_dimension)
        except (TypeError, ValueError):
            return ["Chroma embedding dimension is unreadable: invalid metadata"]
        return [] if dimension == expected else [
            f"Chroma embedding dimension is {dimension}; expected {expected}"
        ]
    try:
        collection = getattr(vectorstore, "_collection", None)
        if collection is None:
            return ["Chroma collection is not available"]
        payload = collection.get(include=["embeddings"], limit=1)
        embeddings = payload.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return ["Chroma collection is empty"]
        dimension = len(embeddings[0])
        return [] if dimension == expected else [
            f"Chroma embedding dimension is {dimension}; expected {expected}"
        ]
    except Exception as error:  # noqa: BLE001 - readiness boundary
        return [f"Chroma embedding dimension is unreadable: {type(error).__name__}"]


def validate_sqlite_path(path: Path) -> list[str]:
    if not path.is_file():
        return ["RETRIEVER_INDEX_PATH does not point to an existing SQLite file"]
    return []


def validate_corpus_directory(path: Path) -> list[str]:
    """Validate the two metadata files required by Interpreter."""

    if not path.is_dir():
        return ["CORPUS_DIR does not point to a directory"]
    missing = [name for name in ("universe.csv", "manifest.jsonl") if not (path / name).is_file()]
    return ["CORPUS_DIR is missing: " + ", ".join(missing)] if missing else []
