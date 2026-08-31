"""Offline deployment-readiness checks for the executable pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import config


def _api_key() -> str:
    return os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()


def validate_environment(mode: str) -> list[str]:
    """Return configuration problems without making a network request."""

    issues: list[str] = []
    if mode not in config.VALID_STAGE2_MODES:
        issues.append(
            "STAGE2_MODE must be one of " + ", ".join(config.VALID_STAGE2_MODES)
        )
    if not _api_key():
        issues.append("CLOVA_API_KEY is not configured")
    if not os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com").strip():
        issues.append("CLOVA_API_HOST is empty")
    return issues


def validate_container_settings(settings: "config.Stage2Settings") -> list[str]:
    """Container mode needs both a Postgres DSN and a Chroma server host."""

    issues: list[str] = []
    if not settings.rdb_url:
        issues.append("STAGE2_RDB_URL is required for STAGE2_MODE=container")
    if not settings.chroma_host:
        issues.append("STAGE2_CHROMA_HOST is required for STAGE2_MODE=container")
    return issues


def raise_if_invalid(issues: list[str]) -> None:
    if issues:
        raise RuntimeError("deployment readiness failed: " + "; ".join(issues))


def validate_fixture(retriever: Any) -> list[str]:
    issues: list[str] = []
    documents = getattr(retriever, "documents", [])
    if not documents:
        return ["fixture contains no documents"]
    for document in documents:
        embedding = document.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != 1024:
            issues.append(f"fixture document {document.get('id', '')} has invalid embedding dimension")
            break
    return issues


def validate_embedding_dimension(vectorstore: Any, expected: int = 1024) -> list[str]:
    """Validate one persisted vector without making a provider request."""

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
        return ["STAGE2_INDEX_PATH does not point to an existing SQLite file"]
    return []


def validate_corpus_directory(path: Path) -> list[str]:
    """Validate the two metadata files required by Stage1."""

    if not path.is_dir():
        return ["CORPUS_DIR does not point to a directory"]
    missing = [name for name in ("universe.csv", "manifest.jsonl") if not (path / name).is_file()]
    return ["CORPUS_DIR is missing: " + ", ".join(missing)] if missing else []
