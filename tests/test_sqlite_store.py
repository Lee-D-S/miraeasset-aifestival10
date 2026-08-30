from __future__ import annotations

from stage2.sqlite_store import SQLiteStage2Repository


def _vector(index: int) -> list[float]:
    values = [0.0] * 1024
    values[index] = 1.0
    return values


def test_sqlite_store_filters_and_searches(tmp_path):
    path = tmp_path / "smoke.db"
    repository = SQLiteStage2Repository(path, query_embedder=lambda _: _vector(0))
    repository.write_rows([
        {
            "id": "chunk-a",
            "doc_id": "doc-a",
            "text": "삼성전자 매출액과 영업이익",
            "source_path": "a.xml",
            "metadata": {"stock_code": "005930", "base_year": 2025, "doc_group": "periodic"},
            "embedding": _vector(0),
        },
        {
            "id": "chunk-b",
            "doc_id": "doc-b",
            "text": "다른 기업의 매출액",
            "source_path": "b.xml",
            "metadata": {"stock_code": "000001", "base_year": 2025, "doc_group": "periodic"},
            "embedding": _vector(1),
        },
    ])

    candidates = repository.filter_candidates({"base_years": [2025]}, limit=10)
    assert {row["id"] for row in candidates} == {"chunk-a", "chunk-b"}
    assert [row["id"] for row in repository.filter_candidates({"corp_names": []}, 10)] == ["chunk-a", "chunk-b"]
    assert [row["id"] for row in repository.keyword_search("영업이익", candidates, 10)] == ["chunk-a"]
    assert [row["id"] for row in repository.vector_search("질의", candidates, 10)] == ["chunk-a"]


def test_sqlite_store_rejects_non_1024_embeddings(tmp_path):
    repository = SQLiteStage2Repository(tmp_path / "smoke.db")
    try:
        repository.write_rows([{"id": "bad", "embedding": [1.0]}])
    except ValueError as error:
        assert "1024" in str(error)
    else:
        raise AssertionError("expected dimension validation error")
