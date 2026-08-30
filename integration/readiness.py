"""Offline deployment-readiness checks for the executable pipeline."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _api_key() -> str:
    return os.getenv("CLOVA_API_KEY", "").strip() or os.getenv("CLOVASTUDIO_API_KEY", "").strip()


def validate_environment(backend: str) -> list[str]:
    """Return configuration problems without making a network request."""

    issues: list[str] = []
    if backend not in {"fixture", "sqlite"}:
        issues.append("STAGE2_BACKEND must be fixture or sqlite")
    if not _api_key():
        issues.append("CLOVA_API_KEY is not configured")
    if not os.getenv("CLOVA_API_HOST", "clovastudio.stream.ntruss.com").strip():
        issues.append("CLOVA_API_HOST is empty")
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
