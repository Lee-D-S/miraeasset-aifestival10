"""Disclosure-aware ingestion, vendored from the reference ``dart_preprocessing``.

Modules:

- ``converters`` -- BS4 table tag -> Markdown / JSON string
- ``parsers``    -- XML / HTML / PDF file -> section-tagged elements
- ``chunker``    -- elements + doc metadata -> chunk dicts (header synthesis,
  row-wise table splitting, per-chunk 연결/별도 detection)
- ``rows``       -- the public entry point: corpus dir -> ``ChunkRow`` list
- ``embeddings`` -- optional local e5 embedder via fastembed / ONNX (no torch)

Install the extra dependencies with ``stage2/ingestion/dart/requirements.txt``.
"""

from __future__ import annotations

from stage2.ingestion.dart.rows import (
    build_dart_chunk_rows,
    iter_dart_chunk_rows,
    load_master_records,
)

__all__ = ["build_dart_chunk_rows", "iter_dart_chunk_rows", "load_master_records"]
