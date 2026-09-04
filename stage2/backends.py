"""Factory helpers for Stage2's local SQLite and Chroma connections.

These factories keep the local transport separate from
``LocalHybridRetriever``'s read-only SQL-building and vector-search code. The
supplied index is opened without creating or modifying database files.
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from pathlib import Path
from typing import Any

from chromadb.config import Settings
from langchain_core.documents import Document
from langchain_chroma import Chroma
from sqlalchemy import Engine, create_engine

_COLLECTION_METADATA = {"hnsw:space": "cosine"}


class _ReadOnlyHnswCollection:
    """Small collection facade backed by Chroma's read-only SQLite metadata."""

    def __init__(self, owner: "ReadOnlyHnswVectorStore") -> None:
        self._owner = owner

    def count(self) -> int:
        return self._owner.collection_count

    def get(
        self,
        *,
        include: list[str] | None = None,
        limit: int | None = None,
        offset: int = 0,
        **_: Any,
    ) -> dict[str, list[Any]]:
        ids = self._owner.collection_ids(offset=offset, limit=limit)
        payload: dict[str, list[Any]] = {"ids": ids}
        if include and "metadatas" in include:
            payload["metadatas"] = [{"chunk_id": identifier} for identifier in ids]
        if include and "embeddings" in include:
            payload["embeddings"] = []
        return payload


class ReadOnlyHnswVectorStore:
    """Query a supplied Chroma HNSW index without opening Chroma's writer API.

    The supplied index was created by an older Chroma persistence format whose
    pickle contains a plain dictionary rather than the current ``PersistentData``
    object.  Loading it through the current Chroma client can migrate SQLite on
    open.  This adapter reads the collection metadata with SQLite ``mode=ro``
    and loads the existing HNSW files directly, never creating directories or
    issuing SQL writes.
    """

    def __init__(
        self,
        persist_directory: str | Path,
        *,
        embedding_function: Any,
        collection_name: str,
    ) -> None:
        import hnswlib

        self.persist_directory = Path(persist_directory).resolve()
        self.embedding_function = embedding_function
        database = self.persist_directory / "chroma.sqlite3"
        if not database.is_file():
            raise FileNotFoundError(f"Chroma SQLite database does not exist: {database}")
        self._database_path = database
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            collection = connection.execute(
                "SELECT id, dimension, config_json_str FROM collections WHERE name = ?",
                (collection_name,),
            ).fetchone()
            if collection is None:
                raise RuntimeError(f"Chroma collection is missing: {collection_name}")
            self.collection_id = str(collection[0])
            self.embedding_dimension = int(collection[1] or 0)
            if self.embedding_dimension <= 0:
                raise RuntimeError("Chroma collection has no valid embedding dimension")
            config = json.loads(collection[2] or "{}")
            space = (
                config.get("vector_index", {})
                .get("hnsw", {})
                .get("space", "cosine")
            )
            if space != "cosine":
                raise RuntimeError(f"unsupported Chroma distance space: {space}")
            segment = connection.execute(
                "SELECT id FROM segments "
                "WHERE collection = ? AND scope = 'VECTOR' "
                "AND type LIKE '%hnsw-local-persisted%'",
                (self.collection_id,),
            ).fetchone()
            if segment is None:
                raise RuntimeError("Chroma HNSW vector segment is missing")
            self.segment_id = str(segment[0])
            self._metadata_segment = self._metadata_segment_id(connection)
            self.collection_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE segment_id = ?",
                    (self._metadata_segment,),
                ).fetchone()[0]
            )
        finally:
            connection.close()
        if self.collection_count <= 0:
            raise RuntimeError("Chroma collection is empty")

        self._index_directory = self.persist_directory / self.segment_id
        metadata_file = self._index_directory / "index_metadata.pickle"
        if not metadata_file.is_file():
            raise FileNotFoundError(f"Chroma HNSW metadata is missing: {metadata_file}")
        with metadata_file.open("rb") as handle:
            persisted = pickle.load(handle)
        id_to_label = (
            persisted.get("id_to_label")
            if isinstance(persisted, dict)
            else getattr(persisted, "id_to_label", None)
        )
        if not isinstance(id_to_label, dict) or not id_to_label:
            raise RuntimeError("Chroma HNSW metadata has no ID map")
        self._id_to_label = {str(identifier): int(label) for identifier, label in id_to_label.items()}
        self._label_to_id = {label: identifier for identifier, label in self._id_to_label.items()}
        if len(self._label_to_id) != len(self._id_to_label):
            raise RuntimeError("Chroma HNSW metadata contains duplicate labels")

        index = hnswlib.Index(space="cosine", dim=self.embedding_dimension)
        try:
            index.load_index(
                str(self._index_directory),
                is_persistent_index=True,
                max_elements=max(len(self._id_to_label), 1),
            )
        except TypeError:
            # hnswlib 0.8.0's Windows wheel predates Chroma's persistent-index
            # keyword but reads the same persisted files without it.
            index.load_index(
                str(self._index_directory),
                max_elements=max(len(self._id_to_label), 1),
            )
        if int(index.get_current_count()) != len(self._id_to_label):
            raise RuntimeError(
                "Chroma HNSW count does not match its ID map: "
                f"{index.get_current_count()} != {len(self._id_to_label)}"
            )
        self._index = index
        self._collection = _ReadOnlyHnswCollection(self)

    @staticmethod
    def _metadata_segment_id(connection: sqlite3.Connection) -> str:
        row = connection.execute(
            "SELECT id FROM segments WHERE scope = 'METADATA' LIMIT 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Chroma metadata segment is missing")
        return str(row[0])

    def collection_ids(self, *, offset: int = 0, limit: int | None = None) -> list[str]:
        sql = "SELECT embedding_id FROM embeddings "
        params: list[Any] = [self._metadata_segment]
        sql += "WHERE segment_id = ? ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit if limit is not None else -1, offset])
        connection = sqlite3.connect(
            f"file:{self._database_path.as_posix()}?mode=ro",
            uri=True,
        )
        try:
            return [str(row[0]) for row in connection.execute(sql, params).fetchall()]
        finally:
            connection.close()

    def similarity_search_with_relevance_scores(
        self,
        query: str,
        *,
        k: int = 4,
        filter: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[tuple[Document, float]]:
        query_vector = [float(value) for value in self.embedding_function.embed_query(query)]
        if len(query_vector) != self.embedding_dimension:
            raise ValueError(
                "query embedding dimension does not match Chroma collection: "
                f"{len(query_vector)} != {self.embedding_dimension}"
            )
        allowed = None
        if filter:
            chunk_filter = filter.get("chunk_id")
            if isinstance(chunk_filter, dict) and "$in" in chunk_filter:
                allowed = {str(value) for value in chunk_filter["$in"]}
        if allowed is None:
            labels, distances = self._index.knn_query(query_vector, k=min(k, len(self._id_to_label)))
            pairs = [(int(label), 1.0 - float(distance)) for label, distance in zip(labels[0], distances[0])]
        else:
            pairs = []
            candidate_labels = [
                self._id_to_label[identifier]
                for identifier in allowed
                if identifier in self._id_to_label
            ]
            if candidate_labels:
                vectors = self._index.get_items(candidate_labels)
                for label, vector in zip(candidate_labels, vectors):
                    score = sum(float(left) * float(right) for left, right in zip(query_vector, vector))
                    pairs.append((label, score))
                pairs.sort(key=lambda item: item[1], reverse=True)
                pairs = pairs[:k]
        return [
            (Document(page_content="", metadata={"chunk_id": self._label_to_id[label]}), score)
            for label, score in pairs
        ]


def local_sqlite_engine(path: str | Path) -> Engine:
    """Create a writable SQLite engine for isolated tests only."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def readonly_sqlite_engine(path: str | Path) -> Engine:
    """Open an existing SQLite file using SQLite's read-only URI mode."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite index does not exist: {resolved}")

    def connect():
        import sqlite3

        return sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=ro",
            uri=True,
        )

    return create_engine("sqlite://", creator=connect)


def local_chroma(
    persist_directory: str | Path,
    *,
    embedding_function: Any,
    collection_name: str = "chunk_vectors",
    create_directory: bool = True,
) -> Any:
    """Open a local Chroma persist directory.

    ``create_directory=False`` is used for the supplied read-only index so a
    typo cannot silently create a new empty Chroma database or collection.
    """

    persist_directory = Path(persist_directory)
    if create_directory:
        persist_directory.mkdir(parents=True, exist_ok=True)
    elif not persist_directory.is_dir():
        raise FileNotFoundError(f"Chroma directory does not exist: {persist_directory}")
    if not create_directory:
        return ReadOnlyHnswVectorStore(
            persist_directory,
            embedding_function=embedding_function,
            collection_name=collection_name,
        )
    client_settings = Settings(
        anonymized_telemetry=False,
        migrations="apply",
    )
    return Chroma(
        persist_directory=str(persist_directory),
        embedding_function=embedding_function,
        collection_name=collection_name,
        collection_metadata=_COLLECTION_METADATA,
        create_collection_if_not_exists=create_directory,
        client_settings=client_settings,
    )


__all__ = [
    "local_chroma",
    "local_sqlite_engine",
    "ReadOnlyHnswVectorStore",
    "readonly_sqlite_engine",
]
