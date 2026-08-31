"""Local hybrid Stage2 retriever: SQLite filtering + Chroma vector search.

Replaces the previous ``SQLiteStage2Repository``, which scanned every row in
Python and computed cosine similarity by hand.  Candidate filtering is now a
real SQL ``WHERE`` clause (ported from the team's reference
``app/tools/rdb_methods.py``), and embedding + similarity + ranking is fully
delegated to :class:`langchain_chroma.Chroma` (ported from
``app/tools/vectordb_methods.py``).  No LLM or tool-calling agent is
involved anywhere in this module — Stage2 still calls these functions
directly and deterministically from ``stage2/node.py``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text

from stage2.backends import local_chroma, local_sqlite_engine
from stage2.embedding import ClovaEmbeddings
from integration.readiness import validate_embedding_dimension
from stage2.retrieval import _tokens

_CHUNKS_TABLE_DDL = """CREATE TABLE IF NOT EXISTS chunks (
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
    is_correction INTEGER,
    report_nm TEXT,
    basis TEXT,
    metadata_json TEXT NOT NULL
)"""

_DELETE_SQL = "DELETE FROM chunks WHERE id = :id"

# Deliberately DELETE+INSERT rather than "INSERT OR REPLACE" (SQLite-only) or
# "ON CONFLICT DO UPDATE" (Postgres-only): plain ANSI SQL that upserts
# identically against a local SQLite file or a Dockerized Postgres RDB.
_INSERT_SQL = """INSERT INTO chunks (
    id, doc_id, chunk_id, text, source_path, corp_name, sector, doc_group,
    doc_subtype, base_year, base_month, rcept_dt, is_correction, report_nm,
    basis, metadata_json
) VALUES (
    :id, :doc_id, :chunk_id, :text, :source_path, :corp_name, :sector, :doc_group,
    :doc_subtype, :base_year, :base_month, :rcept_dt, :is_correction, :report_nm,
    :basis, :metadata_json
)"""

_SELECT_COLUMNS = "id, doc_id, chunk_id, text, source_path, metadata_json"


def _text_or_none(value: Any) -> str | None:
    text_value = str(value).strip() if value is not None else ""
    return text_value or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _normalized_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no"}:
            return False
    return bool(value)


def build_manifest_where_and_params(manifest_filter: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Translate Stage1's ``manifest_filter`` into a real SQL WHERE clause.

    Ported from ``app/tools/rdb_methods.py::build_manifest_where_and_params``
    so candidate narrowing runs as SQL instead of a Python row scan.
    """

    clauses: list[str] = []
    params: dict[str, Any] = {}
    if not manifest_filter:
        return "", params

    corp_names = [str(c).strip() for c in (manifest_filter.get("corp_names") or []) if str(c).strip()]
    if corp_names:
        ors = []
        for index, name in enumerate(corp_names):
            key = f"corp_{index}"
            ors.append(f"corp_name LIKE :{key}")
            params[key] = f"%{name}%"
        clauses.append("(" + " OR ".join(ors) + ")")

    sector = str(manifest_filter.get("sector") or "").strip()
    if not corp_names and sector:
        clauses.append("sector LIKE :sector")
        params["sector"] = f"%{sector}%"

    for column, single_key, candidates_key, prefix in (
        ("doc_group", "doc_group", "doc_group_candidates", "dg"),
        ("doc_subtype", "doc_subtype", "doc_subtype_candidates", "ds"),
    ):
        values = (
            [manifest_filter[single_key]]
            if manifest_filter.get(single_key)
            else list(manifest_filter.get(candidates_key) or [])
        )
        values = [str(value).strip() for value in values if str(value).strip()]
        if values:
            keys = []
            for index, value in enumerate(values):
                key = f"{prefix}_{index}"
                keys.append(f":{key}")
                params[key] = value
            clauses.append(f"{column} IN ({', '.join(keys)})")

    for column, source_key, prefix in (
        ("base_year", "base_years", "by"),
        ("base_month", "base_months", "bm"),
    ):
        values = [value for value in (manifest_filter.get(source_key) or []) if value is not None]
        if values:
            keys = []
            for index, value in enumerate(values):
                key = f"{prefix}_{index}"
                keys.append(f":{key}")
                params[key] = int(value)
            clauses.append(f"{column} IN ({', '.join(keys)})")

    rcept_from = manifest_filter.get("rcept_from")
    if rcept_from:
        clauses.append("rcept_dt >= :rcept_from")
        params["rcept_from"] = str(rcept_from).replace("-", "")
    rcept_to = manifest_filter.get("rcept_to")
    if rcept_to:
        clauses.append("rcept_dt <= :rcept_to")
        params["rcept_to"] = str(rcept_to).replace("-", "")

    is_correction = _normalized_bool(manifest_filter.get("is_correction"))
    if is_correction is not None:
        clauses.append("is_correction = :is_correction")
        params["is_correction"] = 1 if is_correction else 0

    report_terms = [str(term).strip() for term in (manifest_filter.get("report_nm_contains") or []) if str(term).strip()]
    if report_terms:
        ors = []
        for index, term in enumerate(report_terms):
            key = f"rn_{index}"
            ors.append(f"report_nm LIKE :{key}")
            params[key] = f"%{term}%"
        clauses.append("(" + " OR ".join(ors) + ")")

    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where_sql, params


class LocalHybridRetriever:
    """Stage2Retriever backed by a SQL RDB (filtering) + a vector DB (vector search).

    Defaults to a local SQLite file and a local Chroma persist directory.
    Pass ``engine``/``vectorstore`` directly (e.g. from
    :mod:`stage2.backends`'s ``postgres_engine``/``chroma_server``) to point
    the same class at a Dockerized RDB/vector DB instead -- the SQL and
    vector-search code below is identical either way.
    """

    def __init__(
        self,
        sqlite_path: str | Path | None = None,
        *,
        chroma_dir: str | Path | None = None,
        collection_name: str = "stage2_chunks",
        embedding_function: Any | None = None,
        engine: Engine | None = None,
        vectorstore: Any | None = None,
    ):
        self.sqlite_path = Path(sqlite_path) if sqlite_path is not None else None
        if engine is not None:
            self.engine = engine
        elif self.sqlite_path is not None:
            self.engine = local_sqlite_engine(self.sqlite_path)
        else:
            raise ValueError("LocalHybridRetriever requires either sqlite_path or engine")

        if vectorstore is not None:
            self.vectorstore = vectorstore
        else:
            if chroma_dir is None:
                if self.sqlite_path is None:
                    raise ValueError("LocalHybridRetriever requires either chroma_dir or vectorstore")
                chroma_dir = self.sqlite_path.parent / f"{self.sqlite_path.stem}_chroma"
            self.vectorstore = local_chroma(
                chroma_dir,
                embedding_function=embedding_function or ClovaEmbeddings(),
                collection_name=collection_name,
            )
        self._initialized = False

    def readiness_issues(self) -> list[str]:
        """Validate the SQL/vector index without calling the embedding API."""

        issues: list[str] = []
        try:
            self.initialize()
            with self.engine.connect() as connection:
                columns = {str(column["name"]) for column in inspect(self.engine).get_columns("chunks")}
                required = {"id", "doc_id", "chunk_id", "text", "metadata_json"}
                missing = sorted(required - columns)
                if missing:
                    issues.append("SQLite chunks schema missing: " + ", ".join(missing))
                rows = connection.exec_driver_sql("SELECT chunk_id FROM chunks").fetchall()
            sql_ids = {str(row[0]) for row in rows}
            if not sql_ids:
                issues.append("SQLite chunks table is empty")
        except Exception as error:  # noqa: BLE001 - readiness boundary
            return [f"SQLite index is not readable: {type(error).__name__}"]

        try:
            collection = getattr(self.vectorstore, "_collection", None)
            if collection is None:
                return [*issues, "Chroma collection is not available"]
            chroma_ids = {str(value) for value in collection.get(include=[]).get("ids", [])}
            if not chroma_ids:
                issues.append("Chroma collection is empty")
            missing_in_chroma = sql_ids - chroma_ids
            if missing_in_chroma:
                issues.append("Chroma is missing SQLite chunk IDs")
            extra_in_chroma = chroma_ids - sql_ids
            if extra_in_chroma:
                issues.append("Chroma contains chunk IDs absent from SQLite")
            issues.extend(validate_embedding_dimension(self.vectorstore))
        except Exception as error:  # noqa: BLE001 - readiness boundary
            issues.append(f"Chroma index is not readable: {type(error).__name__}")
        return issues

    def manifest_consistency_issues(self, manifest_path: str | Path) -> list[str]:
        """Check that every manifest document is represented in the SQL index."""

        manifest_ids: set[str] = set()
        path = Path(manifest_path)
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        value = json.loads(line).get("doc_id")
                        if value:
                            manifest_ids.add(str(value))
        except (OSError, json.JSONDecodeError):
            return ["manifest.jsonl is not readable"]
        if not manifest_ids:
            return ["manifest.jsonl contains no document IDs"]
        self.initialize()
        with self.engine.connect() as connection:
            indexed_ids = {
                str(row[0])
                for row in connection.execute(text("SELECT DISTINCT doc_id FROM chunks")).fetchall()
            }
        missing = manifest_ids - indexed_ids
        extra = indexed_ids - manifest_ids
        issues = []
        if missing:
            issues.append("manifest contains documents absent from Stage2 index")
        if extra:
            issues.append("Stage2 index contains documents absent from manifest")
        return issues

    def initialize(self) -> None:
        if self._initialized:
            return
        with self.engine.begin() as connection:
            connection.execute(text(_CHUNKS_TABLE_DDL))
        self._initialized = True

    def write_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self._write_rows(rows, use_existing_embeddings=False)

    def write_rows_with_embeddings(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Write rows while reusing already-computed embedding vectors.

        This is intended for offline index migration. Normal ingestion should
        continue to use :meth:`write_rows`, which delegates document embedding
        to the configured provider.
        """
        self._write_rows(rows, use_existing_embeddings=True)

    def _write_rows(self, rows: Sequence[Mapping[str, Any]], *, use_existing_embeddings: bool) -> None:
        self.initialize()
        sql_payload = []
        texts: list[str] = []
        metadatas: list[dict[str, Any]] = []
        ids: list[str] = []
        embeddings: list[list[float]] = []
        for row in rows:
            metadata = dict(row.get("metadata") or {})
            doc_id = str(row.get("doc_id", row["id"]))
            chunk_id = str(row.get("chunk_id") or row["id"])
            text_value = str(row.get("text", ""))
            source_path = str(row.get("source_path", row.get("source", "")))
            sql_payload.append({
                "id": str(row["id"]),
                "doc_id": doc_id,
                "chunk_id": chunk_id,
                "text": text_value,
                "source_path": source_path,
                "corp_name": _text_or_none(metadata.get("corp_name")),
                "sector": _text_or_none(metadata.get("sector")),
                "doc_group": _text_or_none(metadata.get("doc_group")),
                "doc_subtype": _text_or_none(metadata.get("doc_subtype")),
                "base_year": _int_or_none(metadata.get("base_year")),
                "base_month": _int_or_none(metadata.get("base_month")),
                "rcept_dt": _text_or_none(metadata.get("rcept_dt")),
                "is_correction": 1 if metadata.get("is_correction") else 0,
                "report_nm": _text_or_none(metadata.get("report_nm")),
                "basis": _text_or_none(metadata.get("basis")),
                "metadata_json": json.dumps(metadata, ensure_ascii=False),
            })
            texts.append(text_value)
            metadatas.append({**metadata, "chunk_id": chunk_id, "doc_id": doc_id, "source_path": source_path})
            ids.append(chunk_id)
            if use_existing_embeddings:
                raw_embedding = row.get("embedding")
                if not isinstance(raw_embedding, list) or not raw_embedding:
                    raise ValueError(f"row {row.get('id', '')} has no embedding")
                try:
                    embedding = [float(value) for value in raw_embedding]
                except (TypeError, ValueError) as error:
                    raise ValueError(f"row {row.get('id', '')} has an invalid embedding") from error
                if len(embedding) != 1024:
                    raise ValueError(f"row {row.get('id', '')} embedding dimension must be 1024")
                embeddings.append(embedding)

        with self.engine.begin() as connection:
            for payload in sql_payload:
                connection.execute(text(_DELETE_SQL), {"id": payload["id"]})
                connection.execute(text(_INSERT_SQL), payload)
        if texts:
            if use_existing_embeddings:
                collection = getattr(self.vectorstore, "_collection", None)
                if collection is None:
                    raise RuntimeError("vectorstore does not expose a Chroma collection")
                collection.upsert(
                    ids=ids,
                    embeddings=embeddings,
                    metadatas=metadatas,
                    documents=texts,
                )
            else:
                # Chroma computes and stores the embeddings itself via the
                # configured embedding function.
                self.vectorstore.add_texts(texts=texts, metadatas=metadatas, ids=ids)

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
        self.initialize()
        where_sql, params = build_manifest_where_and_params(manifest_filter or {})
        # ``rowid`` is SQLite-only; explicit-key ordering also works on PostgreSQL.
        sql = f"SELECT {_SELECT_COLUMNS} FROM chunks{where_sql} ORDER BY id ASC LIMIT :limit"
        with self.engine.connect() as connection:
            rows = connection.execute(text(sql), {**params, "limit": limit}).mappings().all()
        return [
            {
                "id": row["id"],
                "doc_id": row["doc_id"],
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "source_path": row["source_path"],
                "metadata": json.loads(row["metadata_json"]),
            }
            for row in rows
        ]

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
        query_tokens = _tokens(query)
        ranked = []
        for row in candidates:
            content_tokens = _tokens(row.get("text", ""))
            score = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            if score > 0:
                ranked.append((score, str(row["id"]), row))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(row), "keyword_score": score} for score, _, row in ranked[:limit]]

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[dict[str, Any]]:
        chunk_ids = [str(row.get("chunk_id") or row.get("id")) for row in candidates if row.get("chunk_id") or row.get("id")]
        chunk_ids = chunk_ids[:500]
        if not chunk_ids:
            return []
        search_query = query.strip() if query and query.strip() else "공시 보고서"
        # Embedding computation, cosine similarity, and top-k ranking are all
        # delegated to langchain-chroma; Stage2 does not touch vectors.
        results = self.vectorstore.similarity_search_with_relevance_scores(
            search_query, k=limit, filter={"chunk_id": {"$in": chunk_ids}},
        )
        documents = []
        for document, score in results:
            metadata = dict(document.metadata or {})
            identifier = str(metadata.get("chunk_id", ""))
            documents.append({
                "id": identifier,
                "doc_id": str(metadata.get("doc_id", identifier)),
                "chunk_id": identifier,
                "text": document.page_content,
                "source_path": str(metadata.get("source_path", "")),
                "metadata": metadata,
                "vector_score": float(score),
            })
        return documents


__all__ = ["LocalHybridRetriever", "build_manifest_where_and_params"]
