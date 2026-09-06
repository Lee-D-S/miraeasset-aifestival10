from __future__ import annotations

import json
import pickle
import sqlite3
import sys
from types import SimpleNamespace

import pytest

from integration.readiness import validate_embedding_dimension
from retriever.backends import ReadOnlyHnswVectorStore


class _FakeIndex:
    current_count = 2
    loaded = None

    def __init__(self, *, space, dim):
        self.space = space
        self.dim = dim

    def load_index(self, path, *, max_elements, is_persistent_index=False):
        type(self).loaded = (path, max_elements, is_persistent_index)

    def get_current_count(self):
        return self.current_count

    def get_items(self, labels):
        vectors = {10: [1.0, 0.0], 20: [0.0, 1.0]}
        return [vectors[label] for label in labels]


def _supplied_index(tmp_path, *, dimension=2, collection=True, count=2):
    chroma = tmp_path / "chroma"
    chroma.mkdir()
    db = chroma / "chroma.sqlite3"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE collections (id TEXT PRIMARY KEY, name TEXT, dimension INTEGER, config_json_str TEXT);
        CREATE TABLE segments (id TEXT PRIMARY KEY, type TEXT, scope TEXT, collection TEXT);
        CREATE TABLE embeddings (id INTEGER PRIMARY KEY, segment_id TEXT, embedding_id TEXT);
        """
    )
    if collection:
        connection.execute(
            "INSERT INTO collections VALUES (?, ?, ?, ?)",
            ("collection", "chunk_vectors", dimension, json.dumps({
                "vector_index": {"hnsw": {"space": "cosine"}}
            })),
        )
        connection.execute(
            "INSERT INTO segments VALUES (?, ?, ?, ?)",
            ("vector", "urn:chroma:segment/vector/hnsw-local-persisted", "VECTOR", "collection"),
        )
        connection.execute(
            "INSERT INTO segments VALUES (?, ?, ?, ?)",
            ("metadata", "metadata", "METADATA", "collection"),
        )
        connection.executemany(
            "INSERT INTO embeddings VALUES (?, ?, ?)",
            [(index, "metadata", f"chunk-{index}") for index in range(count)],
        )
    connection.commit()
    connection.close()
    segment_dir = chroma / "vector"
    segment_dir.mkdir()
    with (segment_dir / "index_metadata.pickle").open("wb") as handle:
        pickle.dump(
            {
                "dimensionality": None,
                "total_elements_added": 2,
                "id_to_label": {"chunk-0": 10, "chunk-1": 20},
                "label_to_id": {10: "chunk-0", 20: "chunk-1"},
            },
            handle,
        )
    return chroma


def test_readonly_hnsw_store_loads_legacy_pickle_and_ranks_filtered_candidates(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=_FakeIndex))
    store = ReadOnlyHnswVectorStore(
        _supplied_index(tmp_path),
        embedding_function=SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
        collection_name="chunk_vectors",
    )

    results = store.similarity_search_with_relevance_scores(
        "query",
        k=2,
        filter={"chunk_id": {"$in": ["chunk-1", "chunk-0"]}},
    )

    assert [document.metadata["chunk_id"] for document, _ in results] == [
        "chunk-0",
        "chunk-1",
    ]
    assert store._collection.get(include=[], limit=2)["ids"] == ["chunk-0", "chunk-1"]
    assert _FakeIndex.loaded[2] is True


def test_readonly_hnsw_store_rejects_missing_collection(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=_FakeIndex))
    with pytest.raises(RuntimeError, match="collection is missing"):
        ReadOnlyHnswVectorStore(
            _supplied_index(tmp_path, collection=False),
            embedding_function=SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
            collection_name="chunk_vectors",
        )


def test_readonly_hnsw_store_rejects_empty_collection(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "hnswlib", SimpleNamespace(Index=_FakeIndex))
    with pytest.raises(RuntimeError, match="collection is empty"):
        ReadOnlyHnswVectorStore(
            _supplied_index(tmp_path, count=0),
            embedding_function=SimpleNamespace(embed_query=lambda _: [1.0, 0.0]),
            collection_name="chunk_vectors",
        )


def test_declared_vector_dimension_is_checked_without_embedding_request():
    assert validate_embedding_dimension(SimpleNamespace(embedding_dimension=1024)) == []
    assert validate_embedding_dimension(SimpleNamespace(embedding_dimension=768)) == [
        "Chroma embedding dimension is 768; expected 1024"
    ]
