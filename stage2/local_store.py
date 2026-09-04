"""Read-only hybrid Stage2 retriever: SQL filtering + Chroma vector search.

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

from stage2.backends import local_chroma, local_sqlite_engine, readonly_sqlite_engine
from integration.readiness import validate_embedding_dimension
from stage2.retrieval import _tokens

_VALID_TABLES = frozenset({"chunk_index", "chunks"})
_REQUIRED_COLUMNS = (
    "id",
    "doc_id",
    "chunk_id",
    "text",
    "source_path",
    "metadata_json",
)
_SCALAR_METADATA_COLUMNS = (
    "corp_name",
    "sector",
    "doc_group",
    "doc_subtype",
    "base_year",
    "base_month",
    "rcept_dt",
    "rcept_no",
    "is_correction",
    "report_nm",
    "basis",
    "section_name",
)
_MAX_VECTOR_CANDIDATES = 2_000
_CHROMA_METADATA_SAMPLE = 1_000


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
        # Stage1 resolves aliases to the canonical universe ``corp_name`` before
        # it reaches here, so an exact match is correct and lets the SQL use a
        # ``corp_name`` index. A ``LIKE '%name%'`` predicate cannot.
        ors = []
        for index, name in enumerate(corp_names):
            key = f"corp_{index}"
            ors.append(f"corp_name = :{key}")
            params[key] = name
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

    Defaults to a read-only local SQLite file and a local Chroma persist
    directory.
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
        collection_name: str = "chunk_vectors",
        embedding_function: Any | None = None,
        engine: Engine | None = None,
        vectorstore: Any | None = None,
        table_name: str = "chunk_index",
        read_only: bool = True,
    ):
        if table_name not in _VALID_TABLES:
            raise ValueError(f"unsupported Stage2 SQL table: {table_name}")
        self.sqlite_path = Path(sqlite_path) if sqlite_path is not None else None
        self.table_name = table_name
        self.read_only = read_only
        if engine is not None:
            self.engine = engine
        elif self.sqlite_path is not None:
            self.engine = (
                readonly_sqlite_engine(self.sqlite_path)
                if read_only
                else local_sqlite_engine(self.sqlite_path)
            )
        else:
            raise ValueError("LocalHybridRetriever requires either sqlite_path or engine")

        if vectorstore is not None:
            self.vectorstore = vectorstore
        else:
            if chroma_dir is None:
                if self.sqlite_path is None:
                    raise ValueError("LocalHybridRetriever requires either chroma_dir or vectorstore")
                chroma_dir = self.sqlite_path.parent / f"{self.sqlite_path.stem}_chroma"
            if embedding_function is None:
                raise ValueError(
                    "LocalHybridRetriever requires an explicit Stage2 embedding function"
                )
            self.vectorstore = local_chroma(
                chroma_dir,
                embedding_function=embedding_function,
                collection_name=collection_name,
                create_directory=not read_only,
            )
        self._initialized = False

    def readiness_issues(self) -> list[str]:
        """Validate the SQL/vector index without calling the embedding API."""

        issues: list[str] = []
        try:
            self.initialize()
            with self.engine.connect() as connection:
                columns = {
                    str(column["name"])
                    for column in inspect(self.engine).get_columns(self.table_name)
                }
                required = set(_REQUIRED_COLUMNS)
                missing = sorted(required - columns)
                if missing:
                    issues.append(
                        f"SQLite {self.table_name} schema missing: "
                        + ", ".join(missing)
                    )
                rows = connection.exec_driver_sql(
                    f"SELECT chunk_id FROM {self.table_name}"
                ).fetchall()
            sql_ids = {str(row[0]) for row in rows}
            if not sql_ids:
                issues.append(f"SQLite {self.table_name} table is empty")
        except Exception as error:  # noqa: BLE001 - readiness boundary
            return [f"SQLite index is not readable: {type(error).__name__}"]

        try:
            collection = getattr(self.vectorstore, "_collection", None)
            if collection is None:
                return [*issues, "Chroma collection is not available"]
            chroma_ids: set[str] = set()
            offset = 0
            while True:
                payload = collection.get(include=[], limit=100_000, offset=offset)
                batch_ids = [str(value) for value in payload.get("ids", [])]
                if not batch_ids:
                    break
                chroma_ids.update(batch_ids)
                offset += len(batch_ids)
            metadata_payload = collection.get(
                include=["metadatas"],
                limit=_CHROMA_METADATA_SAMPLE,
            )
            metadata_ids = {
                str(metadata.get("chunk_id"))
                for metadata in metadata_payload.get("metadatas", [])
                if isinstance(metadata, Mapping) and metadata.get("chunk_id")
            }
            sampled_ids = {
                str(value) for value in metadata_payload.get("ids", [])
            }
            if not chroma_ids:
                issues.append("Chroma collection is empty")
            missing_in_chroma = sql_ids - chroma_ids
            if missing_in_chroma:
                issues.append("Chroma is missing SQLite chunk IDs")
            extra_in_chroma = chroma_ids - sql_ids
            if extra_in_chroma:
                issues.append("Chroma contains chunk IDs absent from SQLite")
            if metadata_ids != sampled_ids:
                issues.append("Chroma collection IDs do not match metadata chunk IDs")
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
                for row in connection.execute(
                    text(f"SELECT DISTINCT doc_id FROM {self.table_name}")
                ).fetchall()
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

    def filter_candidates(self, manifest_filter: Mapping[str, Any], limit: int) -> list[dict[str, Any]]:
        self.initialize()
        where_sql, params = build_manifest_where_and_params(manifest_filter or {})
        # Explicit-key ordering works on both SQLite and PostgreSQL.
        sql = (
            f"SELECT {self._select_columns()} FROM {self.table_name}"
            f"{where_sql} ORDER BY id ASC LIMIT :limit"
        )
        with self.engine.connect() as connection:
            rows = connection.execute(text(sql), {**params, "limit": limit}).mappings().all()
        return [self._document_from_row(row) for row in rows]

    @staticmethod
    def _document_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
        raw_metadata = row.get("metadata_json")
        try:
            metadata = json.loads(raw_metadata or "{}")
        except (TypeError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        for key in _SCALAR_METADATA_COLUMNS:
            value = row.get(key)
            if key not in metadata and value not in (None, ""):
                metadata[key] = value
        return {
            "id": str(row.get("id") or row.get("chunk_id") or ""),
            "doc_id": str(row.get("doc_id") or ""),
            "chunk_id": str(row.get("chunk_id") or row.get("id") or ""),
            "text": str(row.get("text") or ""),
            "source_path": str(row.get("source_path") or ""),
            "metadata": metadata,
        }

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
        chunk_ids = chunk_ids[:_MAX_VECTOR_CANDIDATES]
        if not chunk_ids:
            return []
        search_query = query.strip() if query and query.strip() else "공시 보고서"
        # Embedding computation and ranking are delegated to the configured
        # vector backend; Stage2 does not write or transform persisted vectors.
        results = self.vectorstore.similarity_search_with_relevance_scores(
            search_query, k=limit, filter={"chunk_id": {"$in": chunk_ids}},
        )
        candidate_by_id = {
            str(row.get("chunk_id") or row.get("id")): row
            for row in candidates
        }
        documents = []
        for document, score in results:
            metadata = dict(document.metadata or {})
            identifier = str(metadata.get("chunk_id", ""))
            candidate = candidate_by_id.get(identifier)
            if candidate is not None:
                candidate_metadata = dict(candidate.get("metadata") or {})
                candidate_metadata.update(metadata)
                metadata = candidate_metadata
            documents.append({
                "id": identifier,
                "doc_id": str(
                    (candidate or {}).get("doc_id")
                    or metadata.get("doc_id", identifier)
                ),
                "chunk_id": identifier,
                "text": str(
                    (candidate or {}).get("text")
                    or document.page_content
                    or ""
                ),
                "source_path": str(
                    (candidate or {}).get("source_path")
                    or metadata.get("source_path", "")
                ),
                "metadata": metadata,
                "vector_score": float(score),
            })
        return documents


__all__ = ["LocalHybridRetriever", "build_manifest_where_and_params"]
