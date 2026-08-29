import json
from collections.abc import Iterable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from rag.models import ChunkRecord, DocumentRecord


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    corp_name TEXT NOT NULL DEFAULT '',
    corp_code TEXT NOT NULL DEFAULT '',
    document_type TEXT NOT NULL DEFAULT '',
    market TEXT NOT NULL DEFAULT '',
    report_period TEXT NOT NULL DEFAULT '',
    disclosure_date DATE,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    file_extension TEXT NOT NULL DEFAULT '',
    source_group TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS documents_corp_name_idx ON documents (corp_name);
CREATE INDEX IF NOT EXISTS documents_period_idx ON documents (report_period);
CREATE INDEX IF NOT EXISTS documents_type_idx ON documents (document_type);
CREATE INDEX IF NOT EXISTS documents_source_hash_idx ON documents (source_hash);

CREATE TABLE IF NOT EXISTS document_chunks (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    text TEXT NOT NULL,
    embedding vector(1024),
    span_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    source_path TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS document_chunks_document_idx ON document_chunks (document_id);
CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx
    ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id BIGSERIAL PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL,
    total_documents INTEGER NOT NULL DEFAULT 0,
    processed_documents INTEGER NOT NULL DEFAULT 0,
    failed_documents INTEGER NOT NULL DEFAULT 0,
    total_chunks INTEGER NOT NULL DEFAULT 0,
    error_summary JSONB NOT NULL DEFAULT '[]'::jsonb
);
"""


class PostgresStore:
    """Persistence and vector search for the RAG index."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as error:
            raise RuntimeError("Install psycopg[binary] and pgvector before using PostgreSQL.") from error

        with psycopg.connect(self.dsn) as connection:
            register_vector(connection)
            yield connection

    def initialize(self) -> None:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(SCHEMA_SQL)
            connection.commit()

    def upsert_document(self, document: DocumentRecord) -> None:
        query = """
        INSERT INTO documents
            (id, corp_name, corp_code, document_type, market, report_period,
             disclosure_date, source_path, source_hash, file_extension, source_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            corp_name = EXCLUDED.corp_name,
            corp_code = EXCLUDED.corp_code,
            document_type = EXCLUDED.document_type,
            market = EXCLUDED.market,
            report_period = EXCLUDED.report_period,
            disclosure_date = EXCLUDED.disclosure_date,
            source_path = EXCLUDED.source_path,
            source_hash = EXCLUDED.source_hash,
            file_extension = EXCLUDED.file_extension,
            source_group = EXCLUDED.source_group,
            updated_at = now()
        """
        values = (
            document.document_id,
            document.corp_name,
            document.corp_code,
            document.document_type,
            document.market,
            document.report_period,
            document.disclosure_date,
            str(document.source_path),
            document.source_hash,
            document.file_extension,
            document.source_group,
        )
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, values)
            connection.commit()

    def document_is_current(self, document_id: str, source_hash: str) -> bool:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT d.source_hash, COUNT(c.id)
                    FROM documents d
                    LEFT JOIN document_chunks c ON c.document_id = d.id
                    WHERE d.id = %s
                    GROUP BY d.source_hash
                    """,
                    (document_id,),
                )
                row = cursor.fetchone()
        return bool(row and row[0] == source_hash and row[1] > 0)

    def delete_chunks(self, document_id: str) -> None:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            connection.commit()

    def upsert_chunks(self, chunks: Iterable[ChunkRecord]) -> int:
        rows = list(chunks)
        if not rows:
            return 0
        query = """
        INSERT INTO document_chunks
            (id, document_id, chunk_index, text, embedding, span_json, source_path, text_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            text = EXCLUDED.text,
            embedding = EXCLUDED.embedding,
            span_json = EXCLUDED.span_json,
            source_path = EXCLUDED.source_path,
            text_hash = EXCLUDED.text_hash
        """
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(query, [
                    (
                        chunk.chunk_id,
                        chunk.document_id,
                        chunk.chunk_index,
                        chunk.text,
                        chunk.embedding,
                        json.dumps(chunk.span),
                        chunk.source_path,
                        chunk.text_hash,
                    )
                    for chunk in rows
                ])
            connection.commit()
        return len(rows)

    def search(
        self,
        embedding: list[float],
        *,
        limit: int = 20,
        filters: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        conditions = ["c.embedding IS NOT NULL"]
        parameters: list[Any] = [embedding]
        for field in ("corp_name", "corp_code", "document_type", "report_period", "source_group"):
            value = (filters or {}).get(field)
            if value:
                if field == "document_type":
                    conditions.append(f"d.{field} ILIKE %s")
                    parameters.append(f"%{value}%")
                else:
                    conditions.append(f"d.{field} = %s")
                    parameters.append(value)
        parameters.extend([embedding, limit])
        query = f"""
        SELECT c.id, c.text, c.source_path, d.corp_name, d.corp_code,
               d.document_type, d.report_period, d.disclosure_date,
               d.source_group, 1 - (c.embedding <=> %s) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {' AND '.join(conditions)}
        ORDER BY c.embedding <=> %s
        LIMIT %s
        """
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                columns = [description.name for description in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def list_corp_names(self) -> list[str]:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DISTINCT corp_name FROM documents WHERE corp_name <> '' ORDER BY corp_name")
                return [str(row[0]) for row in cursor.fetchall()]
