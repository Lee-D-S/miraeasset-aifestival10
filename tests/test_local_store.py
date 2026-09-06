from __future__ import annotations

import json
import math

import pytest
from langchain_core.embeddings import Embeddings
from sqlalchemy import create_engine, text

from retriever.backends import local_chroma, readonly_sqlite_engine
from retriever.local_store import LocalHybridRetriever

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
    point -- the seam shared by the local SQLite engine and Chroma vector store
    implementations (see retriever/backends.py's local factories)."""
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


_QUERY_AWARE_DDL = """
CREATE TABLE chunk_index (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    text TEXT NOT NULL,
    source_path TEXT NOT NULL,
    corp_name TEXT,
    base_year INTEGER,
    section_name TEXT,
    raw_json_content TEXT,
    metadata_json TEXT NOT NULL
)
"""


def _query_aware_repo(tmp_path) -> LocalHybridRetriever:
    engine = create_engine(f"sqlite:///{tmp_path / 'qa.db'}")
    rows = []
    # 40 filler rows whose ids sort lexicographically before the target.
    for i in range(40):
        rows.append((f"20250311001085_{i}", "회사 개요와 사업의 내용 서술", "I. 회사의 개요", None))
    # The metric-bearing table row: id sorts late, so an id-ordered LIMIT drops it.
    rows.append((
        "20250311001085_900",
        "[삼성전자 | 사업보고서 | 2-2. 연결 손익계산서]\n| 구분 | 제56기 |\n| 영업이익 | 32,725,961 |",
        "2-2. 연결 손익계산서",
        '[{"구분": "영업이익", "제56기": "32,725,961"}]',
    ))
    with engine.begin() as connection:
        connection.execute(text(_QUERY_AWARE_DDL))
        for cid, body, section, raw_json in rows:
            connection.execute(
                text(
                    "INSERT INTO chunk_index (id, doc_id, chunk_id, text, source_path, "
                    "corp_name, base_year, section_name, raw_json_content, metadata_json) "
                    "VALUES (:id, :doc_id, :chunk_id, :text, :sp, :corp, :by, :sec, :rj, :mj)"
                ),
                {
                    "id": cid, "doc_id": "doc", "chunk_id": cid, "text": body, "sp": "x.xml",
                    "corp": "삼성전자", "by": 2024, "sec": section, "rj": raw_json,
                    "mj": json.dumps({"corp_name": "삼성전자", "base_year": 2024}, ensure_ascii=False),
                },
            )
    return LocalHybridRetriever(engine=engine, vectorstore=object())


def test_supplementary_pass_surfaces_a_late_id_metric_row(tmp_path):
    repo = _query_aware_repo(tmp_path)
    manifest = {"corp_names": ["삼성전자"], "base_years": [2024]}
    question = "삼성전자의 2024년 연결기준 손익계산서상 영업이익은 얼마인가?"

    base_only = {row["id"] for row in repo.filter_candidates(manifest, 20)}
    assert "20250311001085_900" not in base_only  # dropped by the id-ordered cut

    with_query = repo.filter_candidates(manifest, 20, query=question)
    ids = {row["id"] for row in with_query}
    assert "20250311001085_900" in ids  # kept by the salient-term pass

    target = next(row for row in with_query if row["id"] == "20250311001085_900")
    assert target["raw_json_content"] and "32,725,961" in target["raw_json_content"]
    assert target["metadata"].get("raw_json_content")  # also reaches Reasoner via metadata


def test_supplementary_pass_ignores_corp_name_only_terms(tmp_path):
    repo = _query_aware_repo(tmp_path)
    manifest = {"corp_names": ["삼성전자"], "base_years": [2024]}
    # Query carries only the corp name -> no discriminating term -> plain base pass.
    only_corp = repo.filter_candidates(manifest, 20, query="삼성전자 삼성전자")
    assert "20250311001085_900" not in {row["id"] for row in only_corp}


def _summary_section_repo(tmp_path) -> LocalHybridRetriever:
    """Many early-id chunks that also match '영업이익', plus one late-id
    요약재무정보 chunk that only the section-priority ordering can reach."""
    engine = create_engine(f"sqlite:///{tmp_path / 'sec.db'}")
    rows = []
    for i in range(30):
        rows.append((
            f"20240312000736_1{i:03d}",
            "본문 서술에서 영업이익 추이를 언급한다",
            "II. 사업의 내용",
            None,
        ))
    rows.append((
        "20240312000736_315",  # sorts after every '_1xxx' id above
        "[삼성전자 | 사업보고서 | 1. 요약재무정보]\n| 구 분 | 제55기 | 제54기 | 제53기 |\n"
        "| 영업이익 | 6,566,976 | 43,376,630 | 51,633,856 |",
        "1. 요약재무정보",
        '[{"구 분": "영업이익", "제55기": "6,566,976", "제54기": "43,376,630", "제53기": "51,633,856"}]',
    ))
    with engine.begin() as connection:
        connection.execute(text(_QUERY_AWARE_DDL))
        for cid, body, section, raw_json in rows:
            connection.execute(
                text(
                    "INSERT INTO chunk_index (id, doc_id, chunk_id, text, source_path, "
                    "corp_name, base_year, section_name, raw_json_content, metadata_json) "
                    "VALUES (:id, :doc_id, :chunk_id, :text, :sp, :corp, :by, :sec, :rj, :mj)"
                ),
                {
                    "id": cid, "doc_id": "doc", "chunk_id": cid, "text": body, "sp": "x.xml",
                    "corp": "삼성전자", "by": 2023, "sec": section, "rj": raw_json,
                    "mj": json.dumps({"corp_name": "삼성전자", "base_year": 2023}, ensure_ascii=False),
                },
            )
    return LocalHybridRetriever(engine=engine, vectorstore=object())


def test_supplementary_pass_floats_summary_section_over_id_order(tmp_path):
    repo = _summary_section_repo(tmp_path)
    manifest = {"corp_names": ["삼성전자"], "base_years": [2023]}
    question = "삼성전자의 최근 3년 영업이익 추이를 알려줘"

    # term_limit for limit=10 is 5; the 요약재무정보 id sorts after all 30 filler
    # rows, so a plain id-ordered supplementary pass would never reach it.
    ids = {row["id"] for row in repo.filter_candidates(manifest, 10, query=question)}
    assert "20240312000736_315" in ids
    target = next(
        row for row in repo.filter_candidates(manifest, 10, query=question)
        if row["id"] == "20240312000736_315"
    )
    assert "6,566,976" in (target["raw_json_content"] or "")


def test_financial_summary_pass_surfaces_chunk_without_a_matching_term(tmp_path):
    """The guaranteed 요약재무정보 pass keeps the chunk even when the query's
    salient term is not in its text (so the term pass would not pull it)."""
    repo = _summary_section_repo(tmp_path)
    manifest = {"corp_names": ["삼성전자"], "base_years": [2023]}
    # "당기순이익" is nowhere in the 요약재무정보 chunk text above.
    ids = {
        row["id"]
        for row in repo.filter_candidates(manifest, 10, query="삼성전자 최근 3년 당기순이익 추이")
    }
    assert "20240312000736_315" in ids
