from __future__ import annotations

import json
import math

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import create_engine, text

from stage2.backends import local_chroma, readonly_sqlite_engine
from stage2.local_store import LocalHybridRetriever

_VOCAB = ["삼성전자", "매출액", "영업이익", "다른", "기업"]

_ROWS = [
    {
        "id": "chunk-a",
        "doc_id": "doc-a",
        "chunk_id": "chunk-a",
        "text": "삼성전자 매출액과 영업이익",
        "source_path": "a.xml",
        "metadata": {"corp_name": "삼성전자", "base_year": 2025, "doc_group": "periodic"},
    },
    {
        "id": "chunk-b",
        "doc_id": "doc-b",
        "chunk_id": "chunk-b",
        "text": "다른 기업의 매출액",
        "source_path": "b.xml",
        "metadata": {"corp_name": "다른기업", "base_year": 2025, "doc_group": "periodic"},
    },
]

_INDEX_DDL = """
CREATE TABLE chunk_index (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    text TEXT NOT NULL,
    source_path TEXT NOT NULL,
    corp_name TEXT,
    sector TEXT,
    doc_group TEXT,
    doc_subtype TEXT,
    base_year INTEGER,
    base_month INTEGER,
    rcept_dt TEXT,
    rcept_no TEXT,
    is_correction INTEGER,
    report_nm TEXT,
    basis TEXT,
    section_name TEXT,
    metadata_json TEXT NOT NULL
)
"""


class _VocabEmbeddings(Embeddings):
    """Deterministic bag-of-vocabulary embedding so vector ranking is testable offline."""

    def _vector(self, text: str) -> list[float]:
        raw = [1.0 if word in text else 0.0 for word in _VOCAB]
        norm = math.sqrt(sum(value * value for value in raw)) or 1.0
        return [value / norm for value in raw]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)

    def embed_documents(self, texts):
        return [self._vector(text) for text in texts]


class _WideVocabEmbeddings(_VocabEmbeddings):
    def _vector(self, text: str) -> list[float]:
        return super()._vector(text) + [0.0] * (1024 - len(_VOCAB))


def _repository(tmp_path) -> LocalHybridRetriever:
    engine = create_engine(f"sqlite:///{tmp_path / 'index.db'}")
    with engine.begin() as connection:
        connection.execute(text(_INDEX_DDL))
        for row in _ROWS:
            metadata = dict(row["metadata"])
            connection.execute(
                text(
                    "INSERT INTO chunk_index "
                    "(id, doc_id, chunk_id, text, source_path, corp_name, "
                    "sector, doc_group, doc_subtype, base_year, base_month, "
                    "is_correction, metadata_json) VALUES "
                    "(:id, :doc_id, :chunk_id, :text, :source_path, :corp_name, "
                    ":sector, :doc_group, :doc_subtype, :base_year, :base_month, "
                    ":is_correction, :metadata_json)"
                ),
                {
                    "id": row["id"],
                    "doc_id": row["doc_id"],
                    "chunk_id": row["chunk_id"],
                    "text": row["text"],
                    "source_path": row["source_path"],
                    "corp_name": metadata.get("corp_name"),
                    "sector": metadata.get("sector"),
                    "doc_group": metadata.get("doc_group"),
                    "doc_subtype": metadata.get("doc_subtype"),
                    "base_year": metadata.get("base_year"),
                    "base_month": metadata.get("base_month"),
                    "is_correction": int(metadata.get("is_correction", False)),
                    "metadata_json": json.dumps(metadata, ensure_ascii=False),
                },
            )
    vectorstore = local_chroma(
        tmp_path / "index_chroma",
        embedding_function=_WideVocabEmbeddings(),
        collection_name="chunk_vectors",
    )
    vectorstore.add_texts(
        texts=[row["text"] for row in _ROWS],
        metadatas=[
            {
                **row["metadata"],
                "chunk_id": row["chunk_id"],
                "doc_id": row["doc_id"],
                "source_path": row["source_path"],
            }
            for row in _ROWS
        ],
        ids=[row["chunk_id"] for row in _ROWS],
    )
    return LocalHybridRetriever(
        engine=engine,
        vectorstore=vectorstore,
        collection_name="chunk_vectors",
        table_name="chunk_index",
        read_only=True,
    )


def test_filter_candidates_runs_as_sql_where_clause(tmp_path):
    repository = _repository(tmp_path)

    both = repository.filter_candidates({"base_years": [2025]}, limit=10)
    assert {row["id"] for row in both} == {"chunk-a", "chunk-b"}

    only_samsung = repository.filter_candidates({"corp_names": ["삼성전자"]}, limit=10)
    assert [row["id"] for row in only_samsung] == ["chunk-a"]

    unfiltered = repository.filter_candidates({}, limit=10)
    assert [row["id"] for row in unfiltered] == ["chunk-a", "chunk-b"]
    assert repository.filter_candidates({"corp_names": ["삼성전자"]}, 1)[0]["metadata"]["corp_name"] == "삼성전자"


def test_filter_candidates_samples_each_requested_year(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'multi_year.db'}")
    with engine.begin() as connection:
        connection.execute(text(_INDEX_DDL))
        rows = [
            ("samsung-2023-a", 2023, "삼성전자 2023년 매출액"),
            ("samsung-2023-b", 2023, "삼성전자 2023년 영업이익"),
            ("samsung-2024", 2024, "삼성전자 2024년 매출액"),
            ("samsung-2025", 2025, "삼성전자 2025년 매출액"),
        ]
        for chunk_id, year, chunk_text in rows:
            connection.execute(
                text(
                    "INSERT INTO chunk_index "
                    "(id, doc_id, chunk_id, text, source_path, corp_name, base_year, metadata_json) "
                    "VALUES (:id, :doc_id, :chunk_id, :text, :source_path, :corp_name, :base_year, :metadata_json)"
                ),
                {
                    "id": chunk_id,
                    "doc_id": chunk_id,
                    "chunk_id": chunk_id,
                    "text": chunk_text,
                    "source_path": f"{chunk_id}.xml",
                    "corp_name": "삼성전자",
                    "base_year": year,
                    "metadata_json": json.dumps(
                        {"corp_name": "삼성전자", "base_year": year},
                        ensure_ascii=False,
                    ),
                },
            )
    repository = LocalHybridRetriever(
        engine=engine,
        vectorstore=local_chroma(
            tmp_path / "multi_year_chroma",
            embedding_function=_WideVocabEmbeddings(),
            collection_name="chunk_vectors",
        ),
        collection_name="chunk_vectors",
        table_name="chunk_index",
        read_only=True,
    )

    candidates = repository.filter_candidates(
        {"corp_names": ["삼성전자"], "base_years": [2023, 2024, 2025]},
        limit=3,
    )

    assert {row["metadata"]["base_year"] for row in candidates} == {2023, 2024, 2025}


def test_keyword_search_is_token_overlap(tmp_path):
    repository = _repository(tmp_path)
    candidates = repository.filter_candidates({}, limit=10)

    assert [row["id"] for row in repository.keyword_search("영업이익", candidates, 10)] == ["chunk-a"]


def test_vector_search_delegates_embedding_similarity_and_sort_to_chroma(tmp_path):
    repository = _repository(tmp_path)
    candidates = repository.filter_candidates({}, limit=10)

    results = repository.vector_search("삼성전자 영업이익 실적", candidates, limit=10)

    assert results
    assert results[0]["id"] == "chunk-a"
    assert "vector_score" in results[0]


def test_vector_search_only_ranks_within_given_candidates(tmp_path):
    repository = _repository(tmp_path)
    restricted = [row for row in repository.filter_candidates({}, 10) if row["id"] == "chunk-b"]

    results = repository.vector_search("삼성전자 영업이익", restricted, limit=10)

    assert [row["id"] for row in results] == ["chunk-b"]


def test_accepts_an_injected_engine_and_vectorstore(tmp_path):
    """Proves the SQL/vector-search code is agnostic to where engine/vectorstore
    point -- the seam a later Dockerized Postgres/Chroma server swap would use
    (see stage2/backends.py's postgres_engine/chroma_server)."""
    repository = _repository(tmp_path)
    candidates = repository.filter_candidates({"corp_names": ["삼성전자"]}, limit=10)
    assert [row["id"] for row in candidates] == ["chunk-a"]

    results = repository.vector_search("삼성전자 매출액", candidates, limit=10)
    assert [row["id"] for row in results] == ["chunk-a"]


def test_requires_sqlite_path_or_engine(tmp_path):
    try:
        LocalHybridRetriever(vectorstore=local_chroma(tmp_path / "chroma", embedding_function=_VocabEmbeddings()))
    except ValueError as error:
        assert "engine" in str(error)
    else:
        raise AssertionError("expected a ValueError when neither sqlite_path nor engine is given")


def test_readonly_sqlite_engine_rejects_writes(tmp_path):
    path = tmp_path / "index.db"
    writable = create_engine(f"sqlite:///{path}")
    with writable.begin() as connection:
        connection.execute(text(_INDEX_DDL))
    writable.dispose()

    readonly = readonly_sqlite_engine(path)
    with pytest.raises(Exception):
        with readonly.begin() as connection:
            connection.execute(text("CREATE TABLE should_not_exist (id TEXT)"))


def test_readonly_chroma_requires_an_existing_collection(tmp_path):
    persist = tmp_path / "chroma"
    local_chroma(
        persist,
        embedding_function=_WideVocabEmbeddings(),
        collection_name="existing",
    )

    with pytest.raises(Exception):
        local_chroma(
            persist,
            embedding_function=_WideVocabEmbeddings(),
            collection_name="missing",
            create_directory=False,
        )


def test_readiness_checks_chroma_metadata_chunk_ids(tmp_path):
    repository = _repository(tmp_path)
    assert repository.readiness_issues() == []

    repository.vectorstore._collection.update(
        ids=["chunk-a"],
        metadatas=[{"chunk_id": "wrong-id"}],
    )
    assert "Chroma collection IDs do not match metadata chunk IDs" in repository.readiness_issues()
