"""Factory helpers for Stage2's SQL RDB and vector DB connections.

Local SQLite + a local Chroma persist directory is the default today. These
factories exist so a later move to a containerized Postgres RDB and/or a
Chroma *server* is a configuration change (a DSN, a host/port) rather than a
rewrite of ``LocalHybridRetriever``'s SQL-building or vector-search code:
that code only ever talks to a SQLAlchemy ``Engine`` and a
``langchain_chroma.Chroma`` instance, never to "SQLite" or "a local
directory" specifically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_chroma import Chroma
from sqlalchemy import Engine, create_engine

_COLLECTION_METADATA = {"hnsw:space": "cosine"}


def local_sqlite_engine(path: str | Path) -> Engine:
    """The default RDB connection: a local SQLite file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{path}")


def postgres_engine(dsn: str) -> Engine:
    """RDB connection for a (typically Dockerized) Postgres instance.

    ``LocalHybridRetriever`` only ever emits portable ANSI SQL (a WHERE
    clause built from plain comparisons/IN lists, and DELETE+INSERT instead
    of SQLite's ``INSERT OR REPLACE``), so it runs unchanged against this
    engine -- only the connection changes. Requires ``psycopg[binary]``
    (already a project dependency) to be installed.
    """

    return create_engine(dsn)


def local_chroma(persist_directory: str | Path, *, embedding_function: Any, collection_name: str = "stage2_chunks") -> Chroma:
    """The default vector DB connection: a local Chroma persist directory."""

    persist_directory = Path(persist_directory)
    persist_directory.mkdir(parents=True, exist_ok=True)
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
    collection_name: str = "stage2_chunks",
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


__all__ = ["chroma_server", "local_chroma", "local_sqlite_engine", "postgres_engine"]
