"""The one data contract every Stage2 ingestion path produces.

A *chunk row* is the seam between "documents on disk" and "the hybrid index".
Any chunker -- the plain whitespace one in :mod:`stage2.ingestion.plain`, the
DART disclosure-aware one in :mod:`stage2.ingestion.dart`, or a future family
-- returns a list of these, and :func:`stage2.ingestion.writer.write_rows`
consumes them unchanged.  There is no per-source "adapter": the row shape is
the contract.  Serving reads the resulting index read-only via
:class:`stage2.local_store.LocalHybridRetriever`; only the writer writes.

``metadata`` carries everything that is not one of the fixed fields below.
The writer promotes a known subset of ``metadata`` keys to typed SQL columns
for the WHERE clause (see :data:`PROMOTED_COLUMNS`); the full dict is also
stored verbatim as ``metadata_json``.
"""

from __future__ import annotations

from typing import Any, TypedDict


class ChunkRow(TypedDict, total=False):
    """One row handed to the hybrid index.

    ``id`` / ``chunk_id`` are the same value in every current chunker; both are
    kept because ``chunk_id`` is also the vector-store record id (so a SQL row
    and its Chroma vector share a key) while ``id`` is the SQL primary key.
    """

    id: str
    doc_id: str
    chunk_id: str
    text: str
    source_path: str
    # Table chunks carry their rows as a JSON string here so the RDB keeps a
    # structured copy of the table independent of the rendered ``text``.
    # ``None`` for prose chunks.
    raw_json_content: str | None
    metadata: dict[str, Any]
    # Optional: only set when a chunker was given a document embedder, and only
    # consumed by ``stage2.ingestion.writer.write_rows_with_embeddings``
    # (offline index migration).
    embedding: list[float]


# Metadata keys the writer lifts into real, typed SQL columns.
PROMOTED_COLUMNS: tuple[str, ...] = (
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

__all__ = ["ChunkRow", "PROMOTED_COLUMNS"]
