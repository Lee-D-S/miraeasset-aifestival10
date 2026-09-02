"""Factory helpers for Stage2's SQL RDB and vector DB connections.

Local SQLite + a local Chroma persist directory is the default today. These
factories keep the transport separate from ``LocalHybridRetriever``'s
read-only SQL-building and vector-search code. The supplied local index is
opened without creating or modifying database files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from sqlalchemy import Engine, create_engine

_COLLECTION_METADATA = {"hnsw:space": "cosine"}


def local_sqlite_engine(path: str | Path) -> Engine:
    """Create a writable SQLite engine for isolated tests only."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def readonly_sqlite_engine(path: str | Path) -> Engine:
    """Open an existing SQLite file using SQLite's read-only URI mode."""

    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"SQLite index does not exist: {resolved}")

    def connect():
        import sqlite3

        return sqlite3.connect(
            f"file:{resolved.as_posix()}?mode=ro",
            uri=True,
        )

    return create_engine("sqlite://", creator=connect)


def postgres_engine(dsn: str) -> Engine:
    """RDB connection for a (typically Dockerized) Postgres instance.

    ``LocalHybridRetriever`` only ever emits portable ANSI SQL (a WHERE
    clause built from plain comparisons/IN lists, and DELETE+INSERT instead
    of SQLite's ``INSERT OR REPLACE``), so it runs unchanged against this
    engine -- only the connection changes. Requires ``psycopg[binary]``
    (already a project dependency) to be installed.
    """

    return create_engine(dsn)


def local_chroma(
    persist_directory: str | Path,
    *,
    embedding_function: Any,
    collection_name: str = "chunk_vectors",
    create_directory: bool = True,
) -> Chroma:
    """Open a local Chroma persist directory.

    ``create_directory=False`` is used for the supplied read-only index so a
    typo cannot silently create a new empty Chroma database.
    """

    persist_directory = Path(persist_directory)
    if create_directory:
        persist_directory.mkdir(parents=True, exist_ok=True)
    elif not persist_directory.is_dir():
        raise FileNotFoundError(f"Chroma directory does not exist: {persist_directory}")
    return Chroma(
        persist_directory=str(persist_directory),
        embedding_function=embedding_function,
        collection_name=collection_name,
        collection_metadata=_COLLECTION_METADATA,
    )


def chroma_server(
    host: str,
    port: int,
    *,
    embedding_function: Any,
    collection_name: str = "chunk_vectors",
    **client_kwargs: Any,
) -> Chroma:
    """Vector DB connection for a networked/Dockerized Chroma server.

    Same ``LocalHybridRetriever.vector_search`` call
    (``similarity_search_with_relevance_scores`` with a ``chunk_id`` filter)
    works against this ``Chroma`` instance unchanged -- only the transport
    (embedded persist directory vs. HTTP client) differs.
    """

    import chromadb

    client = chromadb.HttpClient(host=host, port=port, **client_kwargs)
    return Chroma(
        client=client,
        embedding_function=embedding_function,
        collection_name=collection_name,
        collection_metadata=_COLLECTION_METADATA,
    )


__all__ = [
    "chroma_server",
    "local_chroma",
    "local_sqlite_engine",
    "postgres_engine",
    "readonly_sqlite_engine",
]
