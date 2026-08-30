"""Small SQLite-backed Stage2 retriever for the local smoke corpus."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from stage2.retrieval import matches_manifest_filter


class SQLiteStage2Repository:
    def __init__(self, path: str | Path, *, query_embedder: Callable[[str], Sequence[float]] | None = None):
        self.path = Path(path)
        self.query_embedder = query_embedder

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS chunks (
                    id TEXT PRIMARY KEY,
                    doc_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    embedding_json TEXT NOT NULL
                )"""
            )
            connection.commit()

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.initialize()
        payload = []
        for row in rows:
            embedding = [float(value) for value in row.get("embedding", [])]
            if len(embedding) != 1024:
                raise ValueError(f"embedding for {row.get('id', '')} must have 1024 dimensions")
            payload.append((
                str(row["id"]),
                str(row.get("doc_id", row["id"])),
                str(row.get("text", "")),
                str(row.get("source_path", row.get("source", ""))),
                json.dumps(dict(row.get("metadata", {})), ensure_ascii=False),
                json.dumps(embedding),
            ))
        with sqlite3.connect(self.path) as connection:
            connection.executemany(
                "INSERT OR REPLACE INTO chunks (id, doc_id, text, source_path, metadata_json, embedding_json) VALUES (?, ?, ?, ?, ?, ?)",
                payload,
            )
            connection.commit()

    def _rows(self) -> list[dict[str, Any]]:
        self.initialize()
        with sqlite3.connect(self.path) as connection:
            records = connection.execute("SELECT id, doc_id, text, source_path, metadata_json, embedding_json FROM chunks").fetchall()
        return [
            {
                "id": record[0],
                "doc_id": record[1],
                "text": record[2],
                "source_path": record[3],
                "metadata": json.loads(record[4]),
                "embedding": json.loads(record[5]),
            }
            for record in records
        ]

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[Mapping[str, Any]]:
        return [row for row in self._rows() if matches_manifest_filter(row, manifest_filter)][:limit]

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        query_tokens = set(re.findall(r"\w+", query.lower()))
        ranked = []
        for row in candidates:
            tokens = set(re.findall(r"\w+", str(row.get("text", "")).lower()))
            score = len(query_tokens & tokens) / max(len(query_tokens), 1)
            if score > 0:
                ranked.append((score, str(row["id"]), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(row), "keyword_score": score} for score, _, row in ranked[:limit]]

    @staticmethod
    def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
        if len(left) != len(right):
            raise ValueError(f"embedding dimension mismatch: stored={len(left)}, query={len(right)}")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        if self.query_embedder is None:
            raise RuntimeError("query embedding provider is not configured")
        vector = [float(value) for value in self.query_embedder(query)]
        if len(vector) != 1024:
            raise ValueError(f"query embedding must have 1024 dimensions, got {len(vector)}")
        ranked = []
        for row in candidates:
            score = self._cosine(row["embedding"], vector)
            if score > 0:
                ranked.append((score, str(row["id"]), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(row), "vector_score": score} for score, _, row in ranked[:limit]]


__all__ = ["SQLiteStage2Repository"]
