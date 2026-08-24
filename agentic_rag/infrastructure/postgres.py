from __future__ import annotations

from contextlib import contextmanager
from typing import Any


class PostgresVectorRetriever:
    """pgvector retriever implementing the same contract as LocalVectorRetriever."""

    def __init__(self, dsn: str, embedder: Any):
        self.dsn = dsn
        self.embedder = embedder

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as error:
            raise RuntimeError("Install psycopg[binary] and pgvector for PostgreSQL retrieval") from error
        with psycopg.connect(self.dsn) as connection:
            register_vector(connection)
            yield connection

    def corp_names(self) -> list[str]:
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT DISTINCT corp_name FROM documents WHERE corp_name <> '' ORDER BY corp_name")
            return [str(row[0]) for row in cursor.fetchall()]

    def search(self, query: str, limit: int = 20, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        vector = self.embedder.embed(query) if hasattr(self.embedder, "embed") else self.embedder(query)
        conditions = ["c.embedding IS NOT NULL"]
        params: list[Any] = [vector]
        for field in ("corp_name", "corp_code", "document_type", "report_period", "source_group"):
            value = (filters or {}).get(field)
            if value:
                conditions.append(f"d.{field} ILIKE %s" if field == "document_type" else f"d.{field} = %s")
                params.append(f"%{value}%" if field == "document_type" else value)
        params.extend([vector, limit])
        query_sql = f"""
            SELECT c.id, c.text, c.source_path, d.corp_name, d.corp_code,
                   d.document_type, d.report_period, d.disclosure_date,
                   d.source_group, 1 - (c.embedding <=> %s) AS score
            FROM document_chunks c JOIN documents d ON d.id = c.document_id
            WHERE {' AND '.join(conditions)}
            ORDER BY c.embedding <=> %s LIMIT %s
        """
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(query_sql, params)
            columns = [description.name for description in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        return [{"id": str(row["id"]), "source": str(row["source_path"]), "text": str(row["text"]), "score": max(0.0, min(1.0, float(row.get("score", 0.0)))), "metadata": {key: row.get(key) for key in ("corp_name", "corp_code", "document_type", "report_period", "disclosure_date", "source_group") if row.get(key) is not None}} for row in rows]


class PostgresIndexWriter:
    def __init__(self, dsn: str):
        self.dsn = dsn

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as error:
            raise RuntimeError("Install psycopg[binary] and pgvector for PostgreSQL indexing") from error
        with psycopg.connect(self.dsn) as connection:
            register_vector(connection)
            yield connection

    def initialize(self) -> None:
        schema = """
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, corp_name TEXT NOT NULL DEFAULT '', corp_code TEXT NOT NULL DEFAULT '', document_type TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '', report_period TEXT NOT NULL DEFAULT '', disclosure_date DATE, source_path TEXT NOT NULL, source_hash TEXT NOT NULL, file_extension TEXT NOT NULL DEFAULT '', source_group TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS document_chunks (id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE, chunk_index INTEGER NOT NULL, text TEXT NOT NULL, embedding vector, source_path TEXT NOT NULL, text_hash TEXT NOT NULL, UNIQUE(document_id, chunk_index));
        """
        with self.connection() as connection, connection.cursor() as cursor:
            cursor.execute(schema)
            connection.commit()

    def write_rows(self, rows: list[dict[str, Any]]) -> int:
        with self.connection() as connection, connection.cursor() as cursor:
            for row in rows:
                metadata = row.get("metadata", {})
                document_id = str(metadata.get("source_hash", row["id"]))
                cursor.execute("INSERT INTO documents (id, corp_name, document_type, report_period, source_path, source_hash, file_extension, source_group) VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash", (document_id, metadata.get("corp_name", ""), metadata.get("document_type", ""), metadata.get("report_period", ""), row["source_path"], metadata.get("source_hash", ""), metadata.get("file_extension", ""), metadata.get("source_group", "agentic_rag")))
                cursor.execute("INSERT INTO document_chunks (id, document_id, chunk_index, text, embedding, source_path, text_hash) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, embedding=EXCLUDED.embedding, source_path=EXCLUDED.source_path, text_hash=EXCLUDED.text_hash", (row["id"], document_id, metadata.get("chunk_index", 0), row["text"], row["embedding"], row["source_path"], row["id"]))
            connection.commit()
        return len(rows)
