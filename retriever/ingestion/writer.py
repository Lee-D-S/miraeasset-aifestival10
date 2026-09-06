"""Build-time writer for the Retriever hybrid index.

Serving (``integration.composition`` -> :class:`retriever.local_store.LocalHybridRetriever`)
opens the supplied index strictly read-only.  This module is the other half:
it materializes :class:`retriever.contracts.ChunkRow` rows into the SQL metadata
table and, optionally, a Chroma collection.  Used by
``scripts/build_chunk_index.py`` and the Colab builder; never imported by the
serving path.

The writer accepts a SQLAlchemy SQLite :class:`~sqlalchemy.Engine`. Serving
uses the resulting index read-only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Engine, text

from retriever.contracts import PROMOTED_COLUMNS

# The metadata index table in the local SQLite index.
CHUNK_TABLE = "chunk_index"

EMBEDDING_DIM = 1024

_TABLE_DDL = f"""CREATE TABLE IF NOT EXISTS {CHUNK_TABLE} (
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
    raw_json_content TEXT,
    metadata_json TEXT NOT NULL
)"""

_DELETE_SQL = f"DELETE FROM {CHUNK_TABLE} WHERE id = :id"

# Deliberately use DELETE+INSERT so the local SQLite writer has deterministic
# replacement semantics without relying on database-specific upsert syntax.
_INSERT_SQL = f"""INSERT INTO {CHUNK_TABLE} (
    id, doc_id, chunk_id, text, source_path, corp_name, sector, doc_group,
    doc_subtype, base_year, base_month, rcept_dt, rcept_no, is_correction,
    report_nm, basis, section_name, raw_json_content, metadata_json
) VALUES (
    :id, :doc_id, :chunk_id, :text, :source_path, :corp_name, :sector, :doc_group,
    :doc_subtype, :base_year, :base_month, :rcept_dt, :rcept_no, :is_correction,
    :report_nm, :basis, :section_name, :raw_json_content, :metadata_json
)"""


def _text_or_none(value: Any) -> str | None:
    rendered = str(value).strip() if value is not None else ""
    return rendered or None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() != "" else None
    except (TypeError, ValueError):
        return None


def ensure_schema(engine: Engine) -> None:
    """Create :data:`CHUNK_TABLE` if it does not exist."""

    with engine.begin() as connection:
        connection.execute(text(_TABLE_DDL))


def _sql_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    metadata = dict(row.get("metadata") or {})
    raw_json_content = row.get("raw_json_content")
    payload: dict[str, Any] = {
        "id": str(row["id"]),
        "doc_id": str(row.get("doc_id", row["id"])),
        "chunk_id": str(row.get("chunk_id") or row["id"]),
        "text": str(row.get("text", "")),
        "source_path": str(row.get("source_path", row.get("source", ""))),
        "is_correction": 1 if metadata.get("is_correction") else 0,
        "raw_json_content": str(raw_json_content) if raw_json_content else None,
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
    }
    for column in PROMOTED_COLUMNS:
        if column in ("base_year", "base_month"):
            payload[column] = _int_or_none(metadata.get(column))
        elif column == "is_correction":
            continue
        else:
            payload[column] = _text_or_none(metadata.get(column))
    return payload


def _chroma_record(row: Mapping[str, Any]) -> tuple[str, str, dict[str, Any]]:
    metadata = dict(row.get("metadata") or {})
    chunk_id = str(row.get("chunk_id") or row["id"])
    doc_id = str(row.get("doc_id", row["id"]))
    source_path = str(row.get("source_path", row.get("source", "")))
    raw_json_content = row.get("raw_json_content")
    # Chroma metadata values must be scalar and non-null.
    chroma_metadata = {key: ("" if value is None else value) for key, value in metadata.items()}
    chroma_metadata.update({"chunk_id": chunk_id, "doc_id": doc_id, "source_path": source_path})
    if raw_json_content:
        chroma_metadata["raw_json_content"] = str(raw_json_content)
    return chunk_id, str(row.get("text", "")), chroma_metadata


def write_rows(
    engine: Engine,
    rows: Sequence[Mapping[str, Any]],
    *,
    vectorstore: Any | None = None,
) -> None:
    """Upsert ``rows`` into the SQL table; optionally add them to ``vectorstore``.

    When ``vectorstore`` is given (a :class:`langchain_chroma.Chroma`), it
    computes and stores the document embeddings itself via its configured
    embedding function.
    """

    ensure_schema(engine)
    payloads = [_sql_payload(row) for row in rows]
    with engine.begin() as connection:
        for payload in payloads:
            connection.execute(text(_DELETE_SQL), {"id": payload["id"]})
            connection.execute(text(_INSERT_SQL), payload)

    if vectorstore is None:
        return
    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    for row in rows:
        chunk_id, text_value, metadata = _chroma_record(row)
        ids.append(chunk_id)
        texts.append(text_value)
        metadatas.append(metadata)
    if texts:
        vectorstore.add_texts(texts=texts, metadatas=metadatas, ids=ids)


def write_rows_with_embeddings(
    engine: Engine,
    rows: Sequence[Mapping[str, Any]],
    *,
    vectorstore: Any,
) -> None:
    """Upsert ``rows`` reusing each row's precomputed ``embedding`` vector.

    For offline index migration only -- normal ingestion uses :func:`write_rows`
    and lets Chroma embed the documents.
    """

    ensure_schema(engine)
    payloads = [_sql_payload(row) for row in rows]
    with engine.begin() as connection:
        for payload in payloads:
            connection.execute(text(_DELETE_SQL), {"id": payload["id"]})
            connection.execute(text(_INSERT_SQL), payload)

    ids: list[str] = []
    texts: list[str] = []
    metadatas: list[dict[str, Any]] = []
    embeddings: list[list[float]] = []
    for row in rows:
        chunk_id, text_value, metadata = _chroma_record(row)
        raw_embedding = row.get("embedding")
        if not isinstance(raw_embedding, list) or not raw_embedding:
            raise ValueError(f"row {row.get('id', '')} has no embedding")
        try:
            embedding = [float(value) for value in raw_embedding]
        except (TypeError, ValueError) as error:
            raise ValueError(f"row {row.get('id', '')} has an invalid embedding") from error
        if len(embedding) != EMBEDDING_DIM:
            raise ValueError(f"row {row.get('id', '')} embedding dimension must be {EMBEDDING_DIM}")
        ids.append(chunk_id)
        texts.append(text_value)
        metadatas.append(metadata)
        embeddings.append(embedding)

    if not texts:
        return
    collection = getattr(vectorstore, "_collection", None)
    if collection is None:
        raise RuntimeError("vectorstore does not expose a Chroma collection")
    collection.upsert(ids=ids, embeddings=embeddings, metadatas=metadatas, documents=texts)


__all__ = [
    "CHUNK_TABLE",
    "EMBEDDING_DIM",
    "ensure_schema",
    "write_rows",
    "write_rows_with_embeddings",
]
