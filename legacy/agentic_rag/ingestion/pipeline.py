from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    text: str
    metadata: dict[str, Any]


def read_document(path: Path) -> SourceDocument:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".json", ".html", ".htm"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as error:
            raise RuntimeError("Install pypdf to ingest PDF documents") from error
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    else:
        raise ValueError(f"Unsupported source extension: {suffix}")
    return SourceDocument(path, text, {"file_extension": suffix, "source_group": "agentic_rag"})


def deterministic_chunks(text: str, *, max_chars: int = 1200) -> list[str]:
    paragraphs = [" ".join(part.split()) for part in text.splitlines() if part.strip()]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current} {paragraph}".strip()
    if current:
        chunks.append(current)
    return chunks


def build_local_index(source_root: str, output: str, *, embedder: Any, segmenter: Any | None = None, limit: int | None = None, writer: Any | None = None, failure_log: str | None = None) -> int:
    root = Path(source_root)
    rows: list[dict[str, Any]] = []
    paths = [path for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in {".txt", ".md", ".json", ".html", ".htm", ".pdf"}]
    if limit is not None:
        paths = paths[:limit]
    failures: list[dict[str, str]] = []
    for path in paths:
        try:
            document = read_document(path)
            source_hash = hashlib.sha256(document.text.encode("utf-8")).hexdigest()
            chunks = segmenter.segment(document.text) if segmenter else deterministic_chunks(document.text)
            for index, text in enumerate(chunks):
                chunk_id = hashlib.sha1(f"{source_hash}:{index}:{text}".encode("utf-8")).hexdigest()
                vector = embedder.embed(text) if hasattr(embedder, "embed") else getattr(embedder.embed_text(text), "vector", [])
                rows.append({"id": chunk_id, "text": text, "source_path": str(path), "embedding": vector, "metadata": {**document.metadata, "source_hash": source_hash, "chunk_index": index}})
        except Exception as error:
            failures.append({"path": str(path), "error": f"{type(error).__name__}: {error}"})
    if writer is not None:
        writer.initialize()
        writer.write_rows(rows)
    else:
        target = Path(output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    if failure_log:
        failure_path = Path(failure_log)
        failure_path.parent.mkdir(parents=True, exist_ok=True)
        failure_path.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures and not rows:
        raise RuntimeError(f"All ingestion inputs failed; see {failure_log or 'failure log'}")
    return len(rows)
