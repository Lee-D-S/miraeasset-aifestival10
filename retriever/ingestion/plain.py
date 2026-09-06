"""Plain whitespace chunker: the dependency-free ingestion path.

Reads selected documents, normalizes them to text, and slices deterministic
non-overlapping chunks.  Tables are flattened into text -- for disclosure XML
with real financial tables use :mod:`retriever.ingestion.dart` instead, which
keeps table structure and a JSON copy of every table.

This module stops before embedding generation.  The same chunk rows can later
be passed an injected document embedder, which keeps document and query
embedding providers explicit and interchangeable.  Output rows follow the
:class:`retriever.contracts.ChunkRow` contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


SUPPORTED_SUFFIXES = frozenset({".html", ".htm", ".md", ".txt", ".xml"})


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def read_source_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".xml":
        try:
            root = ElementTree.parse(path).getroot()
        except ElementTree.ParseError:
            try:
                from lxml import etree
            except ImportError as error:
                raise ValueError(f"malformed XML and lxml recovery parser is unavailable: {path}") from error
            parser = etree.XMLParser(recover=True, huge_tree=True, encoding="utf-8")
            root = etree.parse(str(path), parser).getroot()
        return _compact(" ".join(root.itertext()))
    return _compact(path.read_text(encoding="utf-8", errors="replace"))


def chunk_text(text: str, *, max_chars: int = 1200) -> list[str]:
    """Create deterministic, non-overlapping chunks from normalized text."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    words = _compact(text).split(" ")
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _source_files(document_dir: Path) -> list[Path]:
    files = sorted(path for path in document_dir.rglob("*") if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"no supported source files under {document_dir}")
    return files


def load_selection(selection_path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping) or not isinstance(payload.get("documents"), list):
        raise ValueError("selection must contain a documents list")
    return dict(payload)


def build_chunk_rows(
    selection_path: str | Path,
    *,
    source_root: str | Path,
    embedder: Callable[[str], Sequence[float]] | None = None,
    max_chars: int = 1200,
    max_chunks_per_document: int | None = None,
) -> list[dict[str, Any]]:
    """Read only selected documents and return Retriever-compatible chunk rows."""

    selection = load_selection(selection_path)
    root = Path(source_root).resolve()
    if max_chunks_per_document is not None and max_chunks_per_document <= 0:
        raise ValueError("max_chunks_per_document must be positive")
    rows: list[dict[str, Any]] = []
    for selected in selection["documents"]:
        if not isinstance(selected, Mapping):
            raise ValueError("selection document must be an object")
        relative_path = Path(str(selected["file_path"]))
        document_dir = (root / relative_path).resolve()
        if root not in document_dir.parents and document_dir != root:
            raise ValueError(f"selected document escapes source root: {relative_path}")
        combined = "\n".join(read_source_file(path) for path in _source_files(document_dir))
        source_hash = hashlib.sha256(combined.encode("utf-8")).hexdigest()
        metadata = {key: value for key, value in selected.items() if key not in {"file_path", "test_roles"}}
        metadata.update({"source_hash": source_hash, "source_path": str(relative_path)})
        chunks = chunk_text(combined, max_chars=max_chars)
        if max_chunks_per_document is not None:
            chunks = chunks[:max_chunks_per_document]
        for index, text in enumerate(chunks):
            chunk_id = hashlib.sha1(f"{selected['doc_id']}:{source_hash}:{index}:{text}".encode("utf-8")).hexdigest()
            row: dict[str, Any] = {
                "id": chunk_id,
                "doc_id": str(selected["doc_id"]),
                "chunk_id": chunk_id,
                "text": text,
                "source_path": str(relative_path),
                "raw_json_content": None,
                "metadata": {**metadata, "chunk_index": index, "section_name": ""},
            }
            if embedder is not None:
                row["embedding"] = [float(value) for value in embedder(text)]
            rows.append(row)
    return rows


__all__ = ["SUPPORTED_SUFFIXES", "build_chunk_rows", "chunk_text", "load_selection", "read_source_file"]
