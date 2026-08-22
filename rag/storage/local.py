import math
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class LocalVectorRow:
    id: str
    text: str
    source_path: str
    embedding: list[float]
    metadata: dict[str, Any]


class LocalVectorStore:
    """Small dependency-free vector store for local smoke tests."""

    def __init__(self) -> None:
        self.rows: dict[str, LocalVectorRow] = {}

    def upsert(self, rows: Iterable[LocalVectorRow]) -> int:
        materialized = list(rows)
        for row in materialized:
            if not row.embedding:
                raise ValueError(f"Empty embedding: {row.id}")
            self.rows[row.id] = row
        return len(materialized)

    def search(
        self,
        embedding: list[float],
        *,
        limit: int = 20,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        scored = []
        for row in self.rows.values():
            if filters and any(row.metadata.get(key) != value for key, value in filters.items() if value):
                continue
            score = self._cosine_similarity(embedding, row.embedding)
            scored.append({
                "id": row.id,
                "text": row.text,
                "source_path": row.source_path,
                "score": score,
                **row.metadata,
            })
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def save(self, path: str) -> None:
        payload = [
            {
                "id": row.id,
                "text": row.text,
                "source_path": row.source_path,
                "embedding": row.embedding,
                "metadata": row.metadata,
            }
            for row in self.rows.values()
        ]
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @classmethod
    def load(cls, path: str) -> "LocalVectorStore":
        target = Path(path)
        store = cls()
        if not target.exists():
            return store
        payload = json.loads(target.read_text(encoding="utf-8"))
        store.upsert(LocalVectorRow(**item) for item in payload)
        return store

    def __len__(self) -> int:
        return len(self.rows)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("Embedding dimensions do not match")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
