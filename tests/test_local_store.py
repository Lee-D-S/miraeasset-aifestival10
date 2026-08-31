from __future__ import annotations

import math

from langchain_core.embeddings import Embeddings
from sqlalchemy import create_engine

from stage2.backends import local_chroma
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
    repository = LocalHybridRetriever(
        tmp_path / "smoke.db",
        chroma_dir=tmp_path / "smoke_chroma",
        embedding_function=_VocabEmbeddings(),
    )
    repository.write_rows(_ROWS)
    return repository


def test_filter_candidates_runs_as_sql_where_clause(tmp_path):
    repository = _repository(tmp_path)

    both = repository.filter_candidates({"base_years": [2025]}, limit=10)
    assert {row["id"] for row in both} == {"chunk-a", "chunk-b"}

    only_samsung = repository.filter_candidates({"corp_names": ["삼성전자"]}, limit=10)
    assert [row["id"] for row in only_samsung] == ["chunk-a"]

    unfiltered = repository.filter_candidates({}, limit=10)
    assert [row["id"] for row in unfiltered] == ["chunk-a", "chunk-b"]


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
    engine = create_engine("sqlite:///:memory:")
    vectorstore = local_chroma(tmp_path / "injected_chroma", embedding_function=_VocabEmbeddings())
    repository = LocalHybridRetriever(engine=engine, vectorstore=vectorstore)

    repository.write_rows(_ROWS)
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


def test_write_rows_with_embeddings_reuses_vectors_without_embedding_provider(tmp_path):
    repository = LocalHybridRetriever(
        tmp_path / "smoke.db",
        chroma_dir=tmp_path / "smoke_chroma",
        embedding_function=_WideVocabEmbeddings(),
    )
    vector = [0.0] * 1024
    vector[0] = 1.0
    row = {**_ROWS[0], "embedding": vector}

    repository.write_rows_with_embeddings([row])

    assert repository.readiness_issues() == []
    result = repository.vector_search("아무 단어", [row], limit=1)
    assert result[0]["id"] == "chunk-a"


def test_write_rows_with_embeddings_rejects_wrong_dimension(tmp_path):
    repository = LocalHybridRetriever(
        tmp_path / "smoke.db",
        chroma_dir=tmp_path / "smoke_chroma",
        embedding_function=_VocabEmbeddings(),
    )
    try:
        repository.write_rows_with_embeddings([{**_ROWS[0], "embedding": [1.0]}])
    except ValueError as error:
        assert "1024" in str(error)
    else:
        raise AssertionError("expected embedding dimension validation failure")
