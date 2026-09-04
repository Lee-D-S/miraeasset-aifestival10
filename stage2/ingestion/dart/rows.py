"""DART corpus -> :class:`stage2.contracts.ChunkRow` list.

This is the single entry point for the disclosure-aware ingestion path.  It
ports ``dart_preprocessing/preprocesser.py``'s document loop -- join
``universe.csv`` + ``manifest.jsonl``, locate each document's source file,
parse, chunk -- but stops at the chunk-row contract instead of writing to a
DB.  Persistence is handled by the local SQLite/Chroma index builder after
this function returns the chunk-row contract.

The master-data join uses the standard library (``csv`` + ``json``); only
:mod:`stage2.ingestion.dart.parsers` pulls the heavy parsing dependencies.
"""

from __future__ import annotations

import csv
import json
import os
import warnings
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from typing import Any

from stage2.contracts import ChunkRow
from stage2.ingestion.dart.chunker import split_to_chunks
from stage2.ingestion.dart.parsers import parse_docs

_SOURCE_SUFFIXES = {".xml": "xml", ".xhtml": "html", ".html": "html", ".htm": "html", ".pdf": "pdf"}

# universe.csv columns that manifest.jsonl already carries -- drop them from the
# universe side so the manifest value wins on join (matches preprocesser.py).
_UNIVERSE_DUPLICATE_COLUMNS = ("corp_name", "listed_name", "stock_code", "industry", "sector")


def _load_universe(path: Path) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows: dict[str, dict[str, Any]] = {}
        for record in reader:
            code = str(record.get("corp_code") or "").strip()
            if not code:
                continue
            rows[code] = {
                key: value
                for key, value in record.items()
                if key and key not in _UNIVERSE_DUPLICATE_COLUMNS
            }
    return rows


def load_master_records(corpus_dir: str | Path) -> list[dict[str, Any]]:
    """Return one merged metadata record per manifest document (manifest LEFT JOIN universe)."""

    root = Path(corpus_dir)
    universe = _load_universe(root / "universe.csv")
    records: list[dict[str, Any]] = []
    with (root / "manifest.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            code = str(doc.get("corp_code") or "").strip()
            merged = {**universe.get(code, {}), **doc}
            records.append(merged)
    return records


def _selected_doc_ids(doc_ids: Iterable[str] | None, selection_path: str | Path | None) -> set[str] | None:
    ids: set[str] = set()
    if doc_ids:
        ids.update(str(value).strip() for value in doc_ids if str(value).strip())
    if selection_path:
        payload = json.loads(Path(selection_path).read_text(encoding="utf-8"))
        for entry in payload.get("documents", []):
            if isinstance(entry, dict) and entry.get("doc_id"):
                ids.add(str(entry["doc_id"]).strip())
    return ids or None


def _find_source_file(document_dir: Path) -> tuple[Path, str] | None:
    if not document_dir.is_dir():
        return None
    for path in sorted(document_dir.rglob("*")):
        if path.is_file() and path.suffix.lower() in _SOURCE_SUFFIXES:
            return path, _SOURCE_SUFFIXES[path.suffix.lower()]
    return None


def _chunk_row(
    doc_meta: dict[str, Any],
    chunk: dict[str, Any],
    index: int,
    embedder: Callable[[str], Sequence[float]] | None,
) -> ChunkRow:
    rcept_no = str(chunk.get("rcept_no") or doc_meta.get("rcept_no") or "doc").strip()
    chunk_id = f"{rcept_no}_{index}"
    text = str(chunk.get("text_content") or "")
    raw_json = chunk.get("raw_json_content")

    metadata = {key: value for key, value in doc_meta.items() if key != "file_path"}
    metadata.update(
        {
            "section_name": chunk.get("section_name") or "",
            "chunk_type": chunk.get("chunk_type") or "",
            "basis": chunk.get("basis") or "",
            "rcept_no": rcept_no,
        }
    )

    row: ChunkRow = {
        "id": chunk_id,
        "doc_id": str(doc_meta.get("doc_id") or ""),
        "chunk_id": chunk_id,
        "text": text,
        "source_path": str(doc_meta.get("file_path") or ""),
        "raw_json_content": None if raw_json is None else str(raw_json),
        "metadata": metadata,
    }
    if embedder is not None:
        row["embedding"] = [float(value) for value in embedder(text)]
    return row


def _rows_for_document(
    doc_meta: dict[str, Any],
    root_str: str,
    max_chunk_len: int,
    on_missing: str,
    embedder: Callable[[str], Sequence[float]] | None,
) -> list[ChunkRow]:
    """Locate + parse + chunk a single manifest document.

    Self-contained (only module-level helpers + stdlib/parsing deps) so it can
    run in a :class:`~concurrent.futures.ProcessPoolExecutor` worker.
    """

    root = Path(root_str)
    doc_id = str(doc_meta.get("doc_id") or "")
    located = _find_source_file(root / str(doc_meta.get("file_path") or ""))
    if located is None:
        if on_missing == "raise":
            raise FileNotFoundError(f"no source file for {doc_id} under {doc_meta.get('file_path')}")
        return []
    source_file, file_format = located

    parsed = parse_docs(str(source_file), file_format)
    chunks = split_to_chunks(parsed, doc_meta, max_chunk_len=max_chunk_len)
    return [_chunk_row(doc_meta, chunk, index, embedder) for index, chunk in enumerate(chunks)]


def _rows_for_document_task(task: tuple) -> list[ChunkRow]:
    """``ProcessPoolExecutor.map`` shim — unpacks the argument tuple."""

    return _rows_for_document(*task)


def _resolve_workers(max_workers: int | None) -> int:
    if max_workers is None:
        return os.cpu_count() or 1
    if max_workers < 1:
        raise ValueError("max_workers must be >= 1 or None")
    return max_workers


def build_dart_chunk_rows(
    corpus_dir: str | Path,
    *,
    doc_ids: Iterable[str] | None = None,
    selection_path: str | Path | None = None,
    max_chunk_len: int = 1000,
    embedder: Callable[[str], Sequence[float]] | None = None,
    on_missing: str = "skip",
    max_workers: int | None = 1,
) -> list[ChunkRow]:
    """Parse + chunk the DART corpus into :class:`ChunkRow` records.

    ``corpus_dir`` must contain ``universe.csv``, ``manifest.jsonl`` and the
    ``raw/`` document tree.  Pass ``doc_ids`` or ``selection_path`` to index a
    subset; both unset indexes every manifest document.  ``on_missing`` is
    ``"skip"`` (default) or ``"raise"`` for documents whose source file is
    absent.

    ``max_workers`` controls the parse/chunk fan-out over documents (each one is
    independent): ``1`` (default) stays fully sequential, ``None`` uses every CPU
    core, ``N`` uses ``N`` processes.  It is a no-op when ``embedder`` is set,
    since embedders are generally not picklable — embed after the fact instead
    (``LocalHybridRetriever.write_rows`` / the Colab writer both do).
    """

    root = Path(corpus_dir)
    wanted = _selected_doc_ids(doc_ids, selection_path)
    records = load_master_records(root)

    todo = [
        record
        for record in records
        if wanted is None or str(record.get("doc_id") or "") in wanted
    ]
    seen_docs = len(todo)

    workers = _resolve_workers(max_workers)
    if workers != 1 and embedder is not None:
        warnings.warn(
            "build_dart_chunk_rows: max_workers is ignored when embedder is set; parsing sequentially",
            stacklevel=2,
        )
        workers = 1

    rows: list[ChunkRow] = []
    if workers == 1:
        for doc_meta in todo:
            rows.extend(_rows_for_document(doc_meta, str(root), max_chunk_len, on_missing, embedder))
    else:
        from concurrent.futures import ProcessPoolExecutor

        tasks = [(doc_meta, str(root), max_chunk_len, on_missing, None) for doc_meta in todo]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for doc_rows in pool.map(_rows_for_document_task, tasks, chunksize=8):
                rows.extend(doc_rows)

    if wanted is not None and seen_docs < len(wanted):
        missing = wanted - {str(record.get("doc_id") or "") for record in records}
        if missing and on_missing == "raise":
            raise KeyError(f"selected doc_ids not in manifest: {sorted(missing)}")
    return rows


def iter_dart_chunk_rows(
    corpus_dir: str | Path,
    *,
    doc_ids: Iterable[str] | None = None,
    selection_path: str | Path | None = None,
    max_chunk_len: int = 1000,
    on_missing: str = "skip",
    max_workers: int | None = 1,
) -> Iterator[tuple[str, list]]:
    """Yield ``(doc_id, [ChunkRow, ...])`` one manifest document at a time.

    Same join / parse / chunk as :func:`build_dart_chunk_rows`, but streamed so a
    caller can checkpoint after each document (e.g. the Colab builder writing to
    SQLite as it goes).  No ``embedder`` hook — embed downstream.  ``max_workers``
    fans the per-document parse out over processes exactly as
    :func:`build_dart_chunk_rows` does; results are still yielded in manifest
    order.
    """

    root = Path(corpus_dir)
    wanted = _selected_doc_ids(doc_ids, selection_path)
    records = load_master_records(root)
    todo = [
        record
        for record in records
        if wanted is None or str(record.get("doc_id") or "") in wanted
    ]

    workers = _resolve_workers(max_workers)
    if workers == 1:
        for doc_meta in todo:
            yield (
                str(doc_meta.get("doc_id") or ""),
                _rows_for_document(doc_meta, str(root), max_chunk_len, on_missing, None),
            )
    else:
        from concurrent.futures import ProcessPoolExecutor

        tasks = [(doc_meta, str(root), max_chunk_len, on_missing, None) for doc_meta in todo]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for doc_meta, doc_rows in zip(todo, pool.map(_rows_for_document_task, tasks, chunksize=8)):
                yield str(doc_meta.get("doc_id") or ""), doc_rows

    if wanted is not None:
        missing = wanted - {str(record.get("doc_id") or "") for record in records}
        if missing and on_missing == "raise":
            raise KeyError(f"selected doc_ids not in manifest: {sorted(missing)}")


__all__ = ["build_dart_chunk_rows", "iter_dart_chunk_rows", "load_master_records"]
