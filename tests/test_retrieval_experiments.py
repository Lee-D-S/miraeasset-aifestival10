from __future__ import annotations

import json
import sqlite3

import numpy as np

from stage2.backends import readonly_sqlite_engine
from stage2.retrieval_experiments import (
    ExactVectorBackend,
    ExperimentHybridRetriever,
    Fts5KeywordBackend,
    PythonTokenKeywordBackend,
    build_fts5_sidecar,
)


class FakeQueryEmbedder:
    def embed_query(self, _query: str) -> list[float]:
        return [1.0, 0.0]


def _create_source(path):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE chunk_index (
            id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            chunk_id TEXT NOT NULL,
            text TEXT NOT NULL,
            source_path TEXT NOT NULL,
            raw_json_content TEXT,
            metadata_json TEXT,
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
            section_name TEXT
        )
        """
    )
    rows = [
        (
            "1",
            "doc-1",
            "chunk-1",
            "삼성전자 2025년 연결 기준 매출액은 300조원입니다.",
            "doc-1.xml",
            None,
            json.dumps({"corp_name": "삼성전자"}),
            "삼성전자",
            "전자",
            "periodic",
            "annual",
            2025,
            12,
            "20260301",
            "r1",
            0,
            "사업보고서",
            "연결",
            "손익계산서",
        ),
        (
            "2",
            "doc-2",
            "chunk-2",
            "삼성전자 2024년 연결 기준 자산총계는 500조원입니다.",
            "doc-2.xml",
            None,
            json.dumps({"corp_name": "삼성전자"}),
            "삼성전자",
            "전자",
            "periodic",
            "annual",
            2024,
            12,
            "20250301",
            "r2",
            0,
            "사업보고서",
            "연결",
            "재무상태표",
        ),
        (
            "3",
            "doc-3",
            "chunk-3",
            "다른 기업의 2025년 매출액 공시입니다.",
            "doc-3.xml",
            None,
            json.dumps({"corp_name": "다른기업"}),
            "다른기업",
            "전자",
            "periodic",
            "annual",
            2025,
            12,
            "20260302",
            "r3",
            0,
            "사업보고서",
            "연결",
            "손익계산서",
        ),
    ]
    connection.executemany(
        """
        INSERT INTO chunk_index (
            id, doc_id, chunk_id, text, source_path, raw_json_content,
            metadata_json, corp_name, sector, doc_group, doc_subtype,
            base_year, base_month, rcept_dt, rcept_no, is_correction,
            report_nm, basis, section_name
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    connection.commit()
    connection.close()


def _document(identifier: str, content: str, corp_name: str = "삼성전자"):
    return {
        "id": identifier,
        "chunk_id": identifier,
        "doc_id": identifier,
        "text": content,
        "source_path": f"{identifier}.xml",
        "metadata": {"corp_name": corp_name},
    }


def test_python_keyword_backend_matches_current_token_overlap():
    backend = PythonTokenKeywordBackend()
    rows = [
        _document("a", "삼성전자 2025년 매출액"),
        _document("b", "삼성전자 2024년 자산총계"),
    ]

    result = backend.search("삼성전자 2025년 매출액", rows, 2)

    assert result[0]["chunk_id"] == "a"
    assert result[0]["keyword_score"] == 1.0


def test_fts5_sidecar_searches_only_allowed_candidates(tmp_path):
    source = tmp_path / "source.db"
    sidecar = tmp_path / "fts5.sqlite"
    _create_source(source)
    info = build_fts5_sidecar(source, sidecar)
    backend = Fts5KeywordBackend(sidecar)

    rows = [
        _document("chunk-1", "삼성전자 2025년 연결 기준 매출액"),
        _document("chunk-2", "삼성전자 2024년 연결 기준 자산총계"),
    ]
    result = backend.search("매출액", rows, 10)

    assert info["row_count"] == 3
    assert [item["chunk_id"] for item in result] == ["chunk-1"]
    assert result[0]["keyword_score"] == 1.0
    backend.close()


def test_exact_backend_ranks_allowed_candidates(tmp_path):
    vectors_path = tmp_path / "vectors.f32.mmap"
    vector_ids_path = tmp_path / "vector_ids.npy"
    np.asarray([[1.0, 0.0], [0.5, 0.5], [-1.0, 0.0]], dtype=np.float32).tofile(
        vectors_path
    )
    np.save(vector_ids_path, np.asarray(["a", "b", "c"], dtype=np.str_))
    backend = ExactVectorBackend(
        vectors_path,
        vector_ids_path,
        count=3,
        dimension=2,
    )

    result = backend.search(
        [1.0, 0.0],
        [_document("b", "b"), _document("a", "a"), _document("c", "c")],
        2,
    )

    assert [item["chunk_id"] for item in result] == ["a", "b"]
    assert result[0]["vector_score"] == 1.0
    backend.close()


def test_experiment_retriever_preserves_sql_filter_and_branches(tmp_path):
    source = tmp_path / "source.db"
    sidecar = tmp_path / "fts5.sqlite"
    vectors_path = tmp_path / "vectors.f32.mmap"
    vector_ids_path = tmp_path / "vector_ids.npy"
    _create_source(source)
    build_fts5_sidecar(source, sidecar)
    np.asarray([[1.0, 0.0], [0.5, 0.5]], dtype=np.float32).tofile(vectors_path)
    np.save(vector_ids_path, np.asarray(["chunk-1", "chunk-2"], dtype=np.str_))
    vector_backend = ExactVectorBackend(
        vectors_path,
        vector_ids_path,
        count=2,
        dimension=2,
    )
    retriever = ExperimentHybridRetriever(
        engine=readonly_sqlite_engine(source),
        keyword_backend=Fts5KeywordBackend(sidecar),
        vector_backend=vector_backend,
        query_embedder=FakeQueryEmbedder(),
    )

    candidates = retriever.filter_candidates(
        {
            "corp_names": ["삼성전자"],
            "base_years": [2025],
        },
        10,
    )
    keyword = retriever.keyword_search("매출액", candidates, 10)
    vector = retriever.vector_search("매출액", candidates, 10)

    assert [item["chunk_id"] for item in candidates] == ["chunk-1"]
    assert [item["chunk_id"] for item in keyword] == ["chunk-1"]
    assert [item["chunk_id"] for item in vector] == ["chunk-1"]
    assert vector[0]["text"].startswith("삼성전자 2025년")
    assert vector[0]["metadata"]["corp_name"] == "삼성전자"
    assert retriever.readiness_issues() == []
    retriever.close()
