"""Sidecar retrieval backends used by the HNSW replacement experiments.

The production path remains in retriever.local_store. This module keeps the
supplied SQLite/Chroma files read-only and adds experimental keyword and vector
adapters behind the existing RetrieverProtocol contract.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sqlalchemy import Engine, inspect, text

from retriever.backends import ReadOnlyHnswVectorStore
from retriever.embedding import E5Embeddings
from retriever.local_store import (
    _REQUIRED_COLUMNS,
    _SCALAR_METADATA_COLUMNS,
    LocalHybridRetriever,
    build_manifest_where_and_params,
)
from retriever.retrieval import _tokens


EXPERIMENT_PROFILES = (
    "hnsw-python",
    "hnsw-fts5",
    "exact-python",
    "exact-fts5",
    "ivfflat-python",
    "ivfflat-fts5",
    "ivf-sq8-python",
    "ivf-sq8-fts5",
    "ivf-pq-python",
    "ivf-pq-fts5",
)

DEFAULT_NLIST = 4096
DEFAULT_NPROBES = (8, 32, 128)
DEFAULT_PQ_M = 64
DEFAULT_PQ_NBITS = 8
_SQLITE_VARIABLE_LIMIT = 900


class KeywordBackend(Protocol):
    """Keyword branch contract for an experiment retriever."""

    def search(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        ...

    def readiness_issues(self) -> list[str]:
        ...

    def close(self) -> None:
        ...


class VectorBackend(Protocol):
    """Vector branch contract for an experiment retriever."""

    dimension: int

    def search(
        self,
        query_vector: Sequence[float],
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        ...

    def readiness_issues(self) -> list[str]:
        ...

    def close(self) -> None:
        ...


def _chunk_id(row: Mapping[str, Any]) -> str:
    return str(row.get("chunk_id") or row.get("id") or "").strip()


def _source_fingerprint(path: Path) -> dict[str, Any]:
    """Return a cheap immutable fingerprint for large source files."""

    path = path.resolve()
    size = path.stat().st_size
    sample_size = min(size, 1024 * 1024)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(sample_size))
        if size > sample_size:
            handle.seek(max(size - sample_size, 0))
            digest.update(handle.read(sample_size))
    return {
        "path": str(path),
        "size": size,
        "sample_sha256": digest.hexdigest(),
    }


def _git_revision() -> str | None:
    try:
        value = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
    return value or None


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite index does not exist: {resolved}")
    return sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro",
        uri=True,
    )


def resolve_index_paths(index_root: str | Path) -> tuple[Path, Path]:
    """Resolve either local_db or its parent directory."""

    root = Path(index_root).expanduser().resolve()
    if (root / "chunk_index.db").is_file():
        return root / "chunk_index.db", root / "chunk_index_chroma"
    local = root / "local_db"
    return local / "chunk_index.db", local / "chunk_index_chroma"


def _fts5_query(query: str) -> str:
    terms = sorted(_tokens(query))
    if not terms:
        return ""
    escaped = []
    for term in terms:
        # FTS5 unicode61 does not stem Korean particles.  Prefix matching
        # keeps a query such as 매출액 useful for text containing 매출액은.
        escaped.append('"' + term.replace('"', '""') + '"*')
    return " OR ".join(escaped)


class PythonTokenKeywordBackend:
    """The current Python token-overlap keyword implementation."""

    def search(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        ranked: list[tuple[float, str, Mapping[str, Any]]] = []
        for row in candidates:
            content_tokens = _tokens(row.get("text", ""))
            score = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            if score > 0:
                ranked.append((score, _chunk_id(row), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {**dict(row), "keyword_score": float(score)}
            for score, _, row in ranked[: max(limit, 0)]
        ]

    def readiness_issues(self) -> list[str]:
        return []

    def close(self) -> None:
        return None


class Fts5KeywordBackend:
    """Read-only FTS5 sidecar backend with candidate-aware filtering."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser().resolve()
        self._connection: sqlite3.Connection | None = _readonly_sqlite(self.path)
        try:
            table = self._connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'chunk_fts'"
            ).fetchone()
        except sqlite3.Error:
            self.close()
            raise
        if table is None:
            self.close()
            raise RuntimeError(f"FTS5 sidecar is missing chunk_fts: {self.path}")

    def search(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        match_query = _fts5_query(query)
        if not match_query:
            return []
        candidate_by_id = {
            _chunk_id(row): row
            for row in candidates
            if _chunk_id(row)
        }
        candidate_ids = sorted(candidate_by_id)
        if not candidate_ids or self._connection is None:
            return []

        raw_scores: dict[str, float] = {}
        for offset in range(0, len(candidate_ids), _SQLITE_VARIABLE_LIMIT):
            batch = candidate_ids[offset : offset + _SQLITE_VARIABLE_LIMIT]
            placeholders = ", ".join("?" for _ in batch)
            sql = (
                "SELECT chunk_id, bm25(chunk_fts) AS bm25_score "
                "FROM chunk_fts "
                "WHERE chunk_fts MATCH ? "
                f"AND chunk_id IN ({placeholders}) "
                "ORDER BY bm25_score ASC, chunk_id ASC LIMIT ?"
            )
            try:
                rows = self._connection.execute(
                    sql,
                    [match_query, *batch, max(limit, 1)],
                ).fetchall()
            except sqlite3.OperationalError as error:
                raise RuntimeError(f"FTS5 query failed: {error}") from error
            for identifier, bm25_score in rows:
                key = str(identifier)
                raw_scores[key] = max(
                    raw_scores.get(key, 0.0),
                    max(-float(bm25_score), 0.0),
                )

        if not raw_scores:
            return []
        maximum = max(raw_scores.values())
        ranked = sorted(
            raw_scores.items(),
            key=lambda item: (-item[1], item[0]),
        )
        return [
            {
                **dict(candidate_by_id[identifier]),
                "keyword_score": (
                    float(raw_score / maximum) if maximum > 0 else 0.0
                ),
            }
            for identifier, raw_score in ranked[:limit]
            if identifier in candidate_by_id
        ]

    def readiness_issues(self) -> list[str]:
        if self._connection is None:
            return ["FTS5 sidecar connection is closed"]
        try:
            self._connection.execute("SELECT COUNT(*) FROM chunk_fts").fetchone()
        except sqlite3.Error as error:
            return [f"FTS5 sidecar is not readable: {type(error).__name__}: {error}"]
        return []

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None


def build_fts5_sidecar(
    source_db: str | Path,
    output_path: str | Path,
    *,
    table_name: str = "chunk_index",
    batch_size: int = 2_000,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build an FTS5 sidecar without writing to the source SQLite file."""

    if table_name not in {"chunk_index", "chunks"}:
        raise ValueError(f"unsupported SQLite table: {table_name}")
    source_path = Path(source_db).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    if output.exists() and not overwrite:
        raise FileExistsError(f"FTS5 output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()

    source = _readonly_sqlite(source_path)
    target = sqlite3.connect(str(output))
    inserted = 0
    try:
        target.execute("PRAGMA journal_mode=OFF")
        target.execute("PRAGMA synchronous=OFF")
        target.execute(
            "CREATE VIRTUAL TABLE chunk_fts USING fts5("
            "chunk_id UNINDEXED, text, tokenize='unicode61'"
            ")"
        )
        target.execute(
            "CREATE TABLE artifact_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        target.execute(
            "INSERT INTO artifact_meta(key, value) VALUES (?, ?)",
            ("source_db", str(source_path)),
        )
        cursor = source.execute(
            f"SELECT chunk_id, text FROM {table_name} "
            "WHERE chunk_id IS NOT NULL ORDER BY id"
        )
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            target.executemany(
                "INSERT INTO chunk_fts(rowid, chunk_id, text) VALUES (?, ?, ?)",
                [
                    (inserted + index + 1, str(identifier), str(content or ""))
                    for index, (identifier, content) in enumerate(rows)
                ],
            )
            inserted += len(rows)
            target.commit()
        target.execute("INSERT INTO chunk_fts(chunk_fts) VALUES ('optimize')")
        target.execute(
            "INSERT INTO artifact_meta(key, value) VALUES (?, ?)",
            ("row_count", str(inserted)),
        )
        target.commit()
    finally:
        source.close()
        target.close()
    return {
        "path": str(output),
        "row_count": inserted,
        "size": output.stat().st_size,
    }


class HnswVectorBackend:
    """Use the supplied Chroma HNSW files while receiving precomputed queries."""

    def __init__(self, store: ReadOnlyHnswVectorStore) -> None:
        self.store = store
        self.dimension = int(store.embedding_dimension)

    def search(
        self,
        query_vector: Sequence[float],
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != self.dimension:
            raise ValueError(
                f"query dimension mismatch: {query.shape[0] if query.ndim else 0} "
                f"!= {self.dimension}"
            )
        candidate_pairs = [
            (_chunk_id(row), self.store._id_to_label[_chunk_id(row)])
            for row in candidates
            if _chunk_id(row) in self.store._id_to_label
        ]
        if not candidate_pairs:
            return []
        labels = [label for _, label in candidate_pairs]
        vectors = np.asarray(self.store._index.get_items(labels), dtype=np.float32)
        scores = vectors @ query
        ranked = sorted(
            zip(candidate_pairs, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0][0]),
        )
        return [
            {
                "chunk_id": identifier,
                "id": identifier,
                "vector_score": float(score),
            }
            for (identifier, _), score in ranked[:limit]
        ]

    def readiness_issues(self) -> list[str]:
        if self.store._index.get_current_count() <= 0:
            return ["HNSW index is empty"]
        return []

    def close(self) -> None:
        return None


class ExactVectorBackend:
    """Exact inner-product scoring over the supplied vector matrix."""

    def __init__(
        self,
        vectors_path: str | Path,
        vector_ids_path: str | Path,
        *,
        count: int,
        dimension: int,
    ) -> None:
        self.vectors_path = Path(vectors_path).expanduser().resolve()
        self.vector_ids_path = Path(vector_ids_path).expanduser().resolve()
        self.count = int(count)
        self.dimension = int(dimension)
        if not self.vectors_path.is_file():
            raise FileNotFoundError(f"vector matrix is missing: {self.vectors_path}")
        if not self.vector_ids_path.is_file():
            raise FileNotFoundError(f"vector ID map is missing: {self.vector_ids_path}")
        self.vectors = np.memmap(
            self.vectors_path,
            dtype=np.float32,
            mode="r",
            shape=(self.count, self.dimension),
        )
        self.vector_ids = np.load(self.vector_ids_path, allow_pickle=False)
        if len(self.vector_ids) != self.count:
            raise RuntimeError(
                f"vector ID count mismatch: {len(self.vector_ids)} != {self.count}"
            )
        self._position_by_id = {
            str(identifier): index
            for index, identifier in enumerate(self.vector_ids.tolist())
        }

    def search(
        self,
        query_vector: Sequence[float],
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != self.dimension:
            raise ValueError(
                f"query dimension mismatch: {query.shape[0] if query.ndim else 0} "
                f"!= {self.dimension}"
            )
        candidate_pairs = [
            (_chunk_id(row), self._position_by_id[_chunk_id(row)])
            for row in candidates
            if _chunk_id(row) in self._position_by_id
        ]
        if not candidate_pairs:
            return []
        positions = [position for _, position in candidate_pairs]
        scores = np.asarray(self.vectors[positions] @ query, dtype=np.float32)
        ranked = sorted(
            zip(candidate_pairs, scores, strict=True),
            key=lambda item: (-float(item[1]), item[0][0]),
        )
        return [
            {
                "chunk_id": identifier,
                "id": identifier,
                "vector_score": float(score),
            }
            for (identifier, _), score in ranked[:limit]
        ]

    def readiness_issues(self) -> list[str]:
        if self.count <= 0 or self.dimension <= 0:
            return ["exact vector matrix is empty or invalid"]
        return []

    def close(self) -> None:
        self.vectors = None  # type: ignore[assignment]


class FaissVectorBackend:
    """FAISS IVF backend with selector, overfetch, and exact fallback."""

    def __init__(
        self,
        index_path: str | Path,
        vector_ids_path: str | Path,
        *,
        dimension: int,
        nprobe: int = 32,
        exact_fallback: ExactVectorBackend | None = None,
    ) -> None:
        self.index_path = Path(index_path).expanduser().resolve()
        self.vector_ids_path = Path(vector_ids_path).expanduser().resolve()
        self.dimension = int(dimension)
        self.nprobe = max(int(nprobe), 1)
        self.exact_fallback = exact_fallback
        self._faiss = _require_faiss()
        if not self.index_path.is_file():
            raise FileNotFoundError(f"FAISS index is missing: {self.index_path}")
        self.index = self._faiss.read_index(str(self.index_path))
        self.vector_ids = np.load(self.vector_ids_path, allow_pickle=False)
        if int(self.index.d) != self.dimension:
            raise RuntimeError(
                f"FAISS dimension mismatch: {self.index.d} != {self.dimension}"
            )
        if int(self.index.ntotal) != len(self.vector_ids):
            raise RuntimeError(
                f"FAISS count mismatch: {self.index.ntotal} != {len(self.vector_ids)}"
            )
        self._position_by_id = {
            str(identifier): index
            for index, identifier in enumerate(self.vector_ids.tolist())
        }
        self.search_count = 0
        self.fallback_count = 0
        self.selector_count = 0
        self.overfetch_count = 0

    def _pairs(
        self,
        distances: np.ndarray,
        labels: np.ndarray,
        allowed_positions: set[int],
        limit: int,
    ) -> list[tuple[float, str]]:
        pairs: list[tuple[float, str]] = []
        for distance, label in zip(distances[0], labels[0], strict=True):
            position = int(label)
            if position < 0 or position not in allowed_positions:
                continue
            pairs.append((float(distance), str(self.vector_ids[position])))
        pairs.sort(key=lambda item: (-item[0], item[1]))
        return pairs[:limit]

    def _selector_search(
        self,
        query: np.ndarray,
        allowed_positions: set[int],
        limit: int,
    ) -> list[tuple[float, str]] | None:
        faiss = self._faiss
        if not hasattr(faiss, "SearchParametersIVF") or not hasattr(
            faiss, "IDSelectorBatch"
        ):
            return None
        if not hasattr(self.index, "nlist"):
            return None
        try:
            params = faiss.SearchParametersIVF()
            params.nprobe = min(self.nprobe, int(self.index.nlist))
            ids = np.asarray(sorted(allowed_positions), dtype=np.int64)
            params.sel = faiss.IDSelectorBatch(ids)
            distances, labels = self.index.search(
                query.reshape(1, -1),
                min(limit, len(allowed_positions)),
                params,
            )
            self.selector_count += 1
            return self._pairs(distances, labels, allowed_positions, limit)
        except (AttributeError, RuntimeError, TypeError, ValueError):
            return None

    def _overfetch_search(
        self,
        query: np.ndarray,
        allowed_positions: set[int],
        limit: int,
    ) -> list[tuple[float, str]]:
        requested = min(limit, len(allowed_positions))
        found: list[tuple[float, str]] = []
        for fetch_k in (
            max(requested, 32),
            requested * 8,
            requested * 32,
            requested * 128,
        ):
            fetch_k = min(max(fetch_k, requested), int(self.index.ntotal))
            distances, labels = self.index.search(query.reshape(1, -1), fetch_k)
            found = self._pairs(distances, labels, allowed_positions, requested)
            self.overfetch_count += 1
            if len(found) >= requested or fetch_k >= int(self.index.ntotal):
                break
        return found

    def search(
        self,
        query_vector: Sequence[float],
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = np.asarray(query_vector, dtype=np.float32)
        if query.ndim != 1 or query.shape[0] != self.dimension:
            raise ValueError(
                f"query dimension mismatch: {query.shape[0] if query.ndim else 0} "
                f"!= {self.dimension}"
            )
        allowed_positions = {
            self._position_by_id[_chunk_id(row)]
            for row in candidates
            if _chunk_id(row) in self._position_by_id
        }
        if not allowed_positions:
            return []
        requested = min(limit, len(allowed_positions))
        self.search_count += 1
        pairs = self._selector_search(query, allowed_positions, requested)
        if pairs is None:
            pairs = self._overfetch_search(query, allowed_positions, requested)
        if len(pairs) < requested and self.exact_fallback is not None:
            self.fallback_count += 1
            fallback = self.exact_fallback.search(query, candidates, requested)
            return [
                {
                    "chunk_id": _chunk_id(item),
                    "id": _chunk_id(item),
                    "vector_score": float(item["vector_score"]),
                }
                for item in fallback
            ]
        return [
            {
                "chunk_id": identifier,
                "id": identifier,
                "vector_score": float(score),
            }
            for score, identifier in pairs
        ]

    def readiness_issues(self) -> list[str]:
        if int(self.index.ntotal) <= 0:
            return ["FAISS index is empty"]
        return []

    def stats(self) -> dict[str, int]:
        return {
            "search_count": self.search_count,
            "fallback_count": self.fallback_count,
            "selector_count": self.selector_count,
            "overfetch_count": self.overfetch_count,
        }

    def close(self) -> None:
        self.index = None  # type: ignore[assignment]


def _require_faiss() -> Any:
    try:
        import faiss
    except ImportError as error:  # pragma: no cover - optional dependency boundary
        raise RuntimeError(
            "FAISS experiments require faiss-cpu; install "
            "requirements-retrieval-experiments.txt"
        ) from error
    return faiss


class _NoQueryEmbedding:
    """Placeholder accepted by the read-only Chroma loader during extraction."""

    def embed_query(self, _query: str) -> list[float]:
        raise RuntimeError("vector extraction does not embed queries")


@dataclass(frozen=True)
class ArtifactManifest:
    """Validated metadata for one sidecar artifact directory."""

    root: Path
    vector_count: int
    dimension: int
    vector_ids_path: Path
    vectors_path: Path
    fts5_path: Path

    @classmethod
    def load(cls, root: str | Path) -> "ArtifactManifest":
        artifact_root = Path(root).expanduser().resolve()
        manifest_path = artifact_root / "manifest.json"
        if not manifest_path.is_file():
            raise FileNotFoundError(f"artifact manifest is missing: {manifest_path}")
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        vector_count = int(payload.get("vector_count", 0))
        dimension = int(payload.get("dimension", 0))
        if vector_count <= 0 or dimension <= 0:
            raise RuntimeError("artifact manifest has invalid vector shape")
        vectors_path = artifact_root / str(
            payload.get("vectors_path", "vectors.f32.mmap")
        )
        vector_ids_path = artifact_root / str(
            payload.get("vector_ids_path", "vector_ids.npy")
        )
        fts5_path = artifact_root / str(
            payload.get("fts5_path", "fts5.sqlite")
        )
        for path in (vectors_path, vector_ids_path, fts5_path):
            if not path.is_file():
                raise FileNotFoundError(f"artifact file is missing: {path}")
        return cls(
            root=artifact_root,
            vector_count=vector_count,
            dimension=dimension,
            vector_ids_path=vector_ids_path,
            vectors_path=vectors_path,
            fts5_path=fts5_path,
        )


def build_vector_sidecars(
    *,
    index_root: str | Path,
    artifact_root: str | Path,
    collection_name: str = "chunk_vectors",
    table_name: str = "chunk_index",
    nlist: int = DEFAULT_NLIST,
    pq_m: int = DEFAULT_PQ_M,
    pq_nbits: int = DEFAULT_PQ_NBITS,
    batch_size: int = 4_096,
    build_faiss: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Extract supplied HNSW vectors and create exact/FAISS sidecars."""

    db_path, chroma_path = resolve_index_paths(index_root)
    if not chroma_path.is_dir():
        raise FileNotFoundError(f"Chroma directory is missing: {chroma_path}")
    faiss = _require_faiss() if build_faiss else None
    output = Path(artifact_root).expanduser().resolve()
    if output.exists() and any(output.iterdir()) and not overwrite:
        raise FileExistsError(
            f"artifact directory is not empty: {output}; use overwrite=True"
        )
    output.mkdir(parents=True, exist_ok=True)
    store = ReadOnlyHnswVectorStore(
        chroma_path,
        embedding_function=_NoQueryEmbedding(),
        collection_name=collection_name,
    )
    vector_count = len(store._label_to_id)
    dimension = int(store.embedding_dimension)
    vectors_path = output / "vectors.f32.mmap"
    vector_ids_path = output / "vector_ids.npy"
    vector_matrix = np.memmap(
        vectors_path,
        dtype=np.float32,
        mode="w+",
        shape=(vector_count, dimension),
    )
    labels_and_ids = sorted(store._label_to_id.items())
    vector_ids = [identifier for _, identifier in labels_and_ids]
    for start in range(0, vector_count, batch_size):
        batch = labels_and_ids[start : start + batch_size]
        labels = [label for label, _ in batch]
        vectors = np.asarray(store._index.get_items(labels), dtype=np.float32)
        if vectors.shape != (len(batch), dimension):
            raise RuntimeError(
                f"extracted vector shape mismatch: {vectors.shape} != "
                f"({len(batch)}, {dimension})"
            )
        if not np.isfinite(vectors).all():
            raise RuntimeError("extracted vectors contain non-finite values")
        vector_matrix[start : start + len(batch)] = vectors
    vector_matrix.flush()
    del vector_matrix
    np.save(vector_ids_path, np.asarray(vector_ids, dtype=np.str_))

    with _readonly_sqlite(db_path) as source:
        sqlite_row_count = int(
            source.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        )

    fts5_info = build_fts5_sidecar(
        db_path,
        output / "fts5.sqlite",
        table_name=table_name,
        overwrite=overwrite,
    )
    faiss_info: dict[str, Any] = {}
    if build_faiss:
        assert faiss is not None
        if dimension % pq_m != 0:
            raise ValueError(f"PQ M={pq_m} must divide dimension={dimension}")
        vectors = np.memmap(
            vectors_path,
            dtype=np.float32,
            mode="r",
            shape=(vector_count, dimension),
        )
        rng = np.random.default_rng(0)
        sample_size = min(vector_count, max(10_000, nlist * 20))
        sample_indices = rng.choice(vector_count, size=sample_size, replace=False)
        training = np.asarray(vectors[sample_indices], dtype=np.float32)
        faiss_specs = {
            "ivfflat": lambda quantizer: faiss.IndexIVFFlat(
                quantizer,
                dimension,
                nlist,
                faiss.METRIC_INNER_PRODUCT,
            ),
            "ivf_sq8": lambda quantizer: faiss.IndexIVFScalarQuantizer(
                quantizer,
                dimension,
                nlist,
                faiss.ScalarQuantizer.QT_8bit,
                faiss.METRIC_INNER_PRODUCT,
            ),
            "ivf_pq": lambda quantizer: faiss.IndexIVFPQ(
                quantizer,
                dimension,
                nlist,
                pq_m,
                pq_nbits,
                faiss.METRIC_INNER_PRODUCT,
            ),
        }
        for name, factory in faiss_specs.items():
            index_dir = output / name
            index_dir.mkdir(parents=True, exist_ok=True)
            quantizer = faiss.IndexFlatIP(dimension)
            index = factory(quantizer)
            index.train(training)
            for start in range(0, vector_count, batch_size):
                stop = min(start + batch_size, vector_count)
                ids = np.arange(start, stop, dtype=np.int64)
                index.add_with_ids(np.asarray(vectors[start:stop]), ids)
            index_path = index_dir / "index.faiss"
            faiss.write_index(index, str(index_path))
            faiss_info[name] = {
                "path": str(index_path.relative_to(output)),
                "nlist": nlist,
                "vector_count": int(index.ntotal),
                "training_count": sample_size,
            }

    source_files = {
        "sqlite": _source_fingerprint(db_path),
        "chroma_database": _source_fingerprint(chroma_path / "chroma.sqlite3"),
    }
    manifest = {
        "format_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_revision": _git_revision(),
        "source_files": source_files,
        "sqlite_row_count": sqlite_row_count,
        "vector_count": vector_count,
        "dimension": dimension,
        "vector_coverage": vector_count / max(sqlite_row_count, 1),
        "embedding_model": E5Embeddings.MODEL_NAME,
        "query_mode": "raw",
        "collection_name": collection_name,
        "table_name": table_name,
        "vectors_path": vectors_path.name,
        "vector_ids_path": vector_ids_path.name,
        "fts5_path": Path(fts5_info["path"]).name,
        "faiss": faiss_info,
        "faiss_parameters": {
            "nlist": nlist,
            "pq_m": pq_m,
            "pq_nbits": pq_nbits,
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def _parse_profile(profile: str) -> tuple[str, str]:
    normalized = str(profile).strip().lower()
    if normalized not in EXPERIMENT_PROFILES:
        raise ValueError(
            f"unsupported experiment profile: {normalized}; choose one of "
            f"{', '.join(EXPERIMENT_PROFILES)}"
        )
    keyword = "fts5" if normalized.endswith("-fts5") else "python"
    vector = normalized[: -len("-" + keyword)]
    return vector, keyword


def build_experiment_retriever(
    *,
    profile: str,
    index_root: str | Path,
    artifact_root: str | Path,
    engine: Engine,
    query_embedder: Any,
    collection_name: str = "chunk_vectors",
    table_name: str = "chunk_index",
    nprobe: int = 32,
) -> "ExperimentHybridRetriever":
    """Build one profile without changing production configuration."""

    vector_name, keyword_name = _parse_profile(profile)
    artifact = ArtifactManifest.load(artifact_root)
    keyword_backend: KeywordBackend = (
        Fts5KeywordBackend(artifact.fts5_path)
        if keyword_name == "fts5"
        else PythonTokenKeywordBackend()
    )
    exact_backend = None
    if vector_name != "hnsw":
        exact_backend = ExactVectorBackend(
            artifact.vectors_path,
            artifact.vector_ids_path,
            count=artifact.vector_count,
            dimension=artifact.dimension,
        )
    if vector_name == "hnsw":
        _, chroma_path = resolve_index_paths(index_root)
        store = ReadOnlyHnswVectorStore(
            chroma_path,
            embedding_function=_NoQueryEmbedding(),
            collection_name=collection_name,
        )
        vector_backend: VectorBackend = HnswVectorBackend(store)
    elif vector_name == "exact":
        assert exact_backend is not None
        vector_backend = exact_backend
    else:
        assert exact_backend is not None
        index_path = artifact.root / vector_name.replace("-", "_") / "index.faiss"
        vector_backend = FaissVectorBackend(
            index_path,
            artifact.vector_ids_path,
            dimension=artifact.dimension,
            nprobe=nprobe,
            exact_fallback=exact_backend,
        )
    return ExperimentHybridRetriever(
        engine=engine,
        keyword_backend=keyword_backend,
        vector_backend=vector_backend,
        query_embedder=query_embedder,
        table_name=table_name,
    )


class ExperimentHybridRetriever:
    """SQLite metadata filter plus one experimental keyword/vector pair."""

    def __init__(
        self,
        *,
        engine: Engine,
        keyword_backend: KeywordBackend,
        vector_backend: VectorBackend,
        query_embedder: Any,
        table_name: str = "chunk_index",
    ) -> None:
        if table_name not in {"chunk_index", "chunks"}:
            raise ValueError(f"unsupported Retriever SQL table: {table_name}")
        self.engine = engine
        self.keyword_backend = keyword_backend
        self.vector_backend = vector_backend
        self.query_embedder = query_embedder
        self.table_name = table_name
        self._initialized = False
        self._table_columns: set[str] = set()

    def initialize(self) -> None:
        if self._initialized:
            return
        inspector = inspect(self.engine)
        tables = set(inspector.get_table_names())
        if self.table_name not in tables:
            raise RuntimeError(
                f"SQLite index is missing required table: {self.table_name}"
            )
        self._table_columns = {
            str(column["name"])
            for column in inspector.get_columns(self.table_name)
        }
        missing = set(_REQUIRED_COLUMNS) - self._table_columns
        if missing:
            raise RuntimeError(
                f"SQLite {self.table_name} schema missing: "
                + ", ".join(sorted(missing))
            )
        self._initialized = True

    def _select_columns(self) -> str:
        columns = [
            column
            for column in (
                *_REQUIRED_COLUMNS,
                *_SCALAR_METADATA_COLUMNS,
            )
            if column in self._table_columns
        ]
        return ", ".join(dict.fromkeys(columns))

    def filter_candidates(
        self,
        manifest_filter: Mapping[str, Any],
        limit: int,
        *,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        del query  # experiment sidecar keeps the plain id-ordered candidate pass
        self.initialize()
        where_sql, params = build_manifest_where_and_params(manifest_filter or {})
        sql = (
            f"SELECT {self._select_columns()} FROM {self.table_name}"
            f"{where_sql} ORDER BY id ASC LIMIT :limit"
        )
        with self.engine.connect() as connection:
            rows = connection.execute(
                text(sql),
                {**params, "limit": max(int(limit), 0)},
            ).mappings().all()
        return [LocalHybridRetriever._document_from_row(row) for row in rows]

    def keyword_search(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        return self.keyword_backend.search(query, candidates, limit)

    def vector_search(
        self,
        query: str,
        candidates: Sequence[Mapping[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        query_vector = self.query_embedder.embed_query(query)
        if len(query_vector) != int(self.vector_backend.dimension):
            raise ValueError(
                "query embedding dimension does not match vector backend: "
                f"{len(query_vector)} != {self.vector_backend.dimension}"
            )
        results = self.vector_backend.search(query_vector, candidates, limit)
        candidate_by_id = {
            _chunk_id(row): row
            for row in candidates
            if _chunk_id(row)
        }
        enriched: list[dict[str, Any]] = []
        for result in results:
            identifier = _chunk_id(result)
            candidate = candidate_by_id.get(identifier)
            if candidate is None:
                enriched.append(dict(result))
                continue
            merged = {**dict(candidate), **dict(result)}
            candidate_metadata = dict(candidate.get("metadata") or {})
            result_metadata = result.get("metadata")
            if isinstance(result_metadata, Mapping):
                candidate_metadata.update(result_metadata)
            merged["metadata"] = candidate_metadata
            enriched.append(merged)
        return enriched

    def readiness_issues(self) -> list[str]:
        issues: list[str] = []
        try:
            self.initialize()
            with self.engine.connect() as connection:
                count = int(
                    connection.execute(
                        text(f"SELECT COUNT(*) FROM {self.table_name}")
                    ).scalar_one()
                )
            if count <= 0:
                issues.append(f"SQLite {self.table_name} table is empty")
        except Exception as error:  # noqa: BLE001 - readiness boundary
            issues.append(f"SQLite index is not readable: {type(error).__name__}")
        issues.extend(self.keyword_backend.readiness_issues())
        issues.extend(self.vector_backend.readiness_issues())
        return issues

    def backend_stats(self) -> dict[str, Any]:
        stats = {}
        if hasattr(self.vector_backend, "stats"):
            stats["vector"] = self.vector_backend.stats()
        return stats

    def close(self) -> None:
        self.keyword_backend.close()
        self.vector_backend.close()
        self.engine.dispose()


__all__ = [
    "ArtifactManifest",
    "DEFAULT_NLIST",
    "DEFAULT_NPROBES",
    "DEFAULT_PQ_M",
    "DEFAULT_PQ_NBITS",
    "EXPERIMENT_PROFILES",
    "ExactVectorBackend",
    "ExperimentHybridRetriever",
    "FaissVectorBackend",
    "Fts5KeywordBackend",
    "HnswVectorBackend",
    "KeywordBackend",
    "PythonTokenKeywordBackend",
    "VectorBackend",
    "build_experiment_retriever",
    "build_fts5_sidecar",
    "build_vector_sidecars",
    "resolve_index_paths",
]
