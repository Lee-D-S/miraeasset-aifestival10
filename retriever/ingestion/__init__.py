"""Retriever ingestion: documents on disk -> :class:`retriever.contracts.ChunkRow` list.

Two chunkers, one output contract:

- :func:`build_chunk_rows` (``plain``) -- dependency-free whitespace slicing,
  used by the CLOVA smoke-index scripts.  Flattens tables into text.
- :func:`build_dart_chunk_rows` (``dart``) -- disclosure-aware parsing +
  chunking ported from the reference ``dart_preprocessing`` package: synthesized
  ``[corp | report | section]`` headers, row-wise table splitting with a JSON
  copy of every table, per-chunk 연결/별도 basis detection.  Needs the extra
  dependencies in ``retriever/ingestion/dart/requirements.txt``.

Both feed :func:`retriever.ingestion.writer.write_rows` unchanged.
"""

from __future__ import annotations

from retriever.contracts import ChunkRow, PROMOTED_COLUMNS
from retriever.ingestion.plain import (
    build_chunk_rows,
    chunk_text,
    load_selection,
    read_source_file,
)


def build_dart_chunk_rows(*args, **kwargs):
    """Lazy proxy for :func:`retriever.ingestion.dart.build_dart_chunk_rows`.

    Imported on call so ``retriever.ingestion`` stays importable without the
    DART parsing dependencies (bs4/lxml/pdfplumber) installed.
    """

    from retriever.ingestion.dart import build_dart_chunk_rows as _impl

    return _impl(*args, **kwargs)


def iter_dart_chunk_rows(*args, **kwargs):
    """Lazy proxy for :func:`retriever.ingestion.dart.iter_dart_chunk_rows`."""

    from retriever.ingestion.dart import iter_dart_chunk_rows as _impl

    return _impl(*args, **kwargs)


__all__ = [
    "ChunkRow",
    "PROMOTED_COLUMNS",
    "build_chunk_rows",
    "build_dart_chunk_rows",
    "chunk_text",
    "iter_dart_chunk_rows",
    "load_selection",
    "read_source_file",
]
