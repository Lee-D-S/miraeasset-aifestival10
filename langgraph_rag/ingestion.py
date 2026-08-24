"""Corpus scanning and document normalization owned by the LangGraph backend."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as element_tree
from dataclasses import dataclass
from datetime import date
from html import unescape
from pathlib import Path


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalize_path(value: str | Path) -> str:
    return unicodedata.normalize("NFC", str(value))


def sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: normalize_path(item)):
        digest.update(normalize_path(path).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    source_path: Path
    source_hash: str
    text: str
    corp_name: str = ""
    corp_code: str = ""
    document_type: str = ""
    market: str = ""
    report_period: str = ""
    disclosure_date: date | None = None
    source_group: str = ""
    file_extension: str = ""


def _parse_date(value: object) -> date | None:
    raw = unicodedata.normalize("NFC", str(value or "")).replace(".", "-").replace("/", "-")
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def _read_json(path: Path) -> str:
    value = json.loads(path.read_text(encoding="utf-8"))
    strings: list[str] = []

    def collect(item: object) -> None:
        if isinstance(item, str):
            strings.append(item)
        elif isinstance(item, dict):
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)

    collect(value)
    return "\n".join(strings)


def _read_xml(path: Path) -> str:
    try:
        root = element_tree.parse(path).getroot()
        return "\n".join(text for text in root.itertext() if text and text.strip())
    except element_tree.ParseError:
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", path.read_bytes())
        try:
            root = element_tree.fromstring(cleaned)
            return "\n".join(text for text in root.itertext() if text and text.strip())
        except element_tree.ParseError:
            return re.sub(rb"<[^>]*>", b"\n", cleaned).decode("utf-8", errors="replace")


def read_document(path: Path) -> str:
    extension = path.suffix.lower()
    if extension == ".json":
        text = _read_json(path)
    elif extension == ".xml":
        text = _read_xml(path)
    elif extension in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    elif extension in {".html", ".htm"}:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser").get_text("\n")
    elif extension == ".pdf":
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return normalize_text(unescape(text))


class ManifestIndex:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path
        self.entries = self._load()

    def _load(self) -> list[dict]:
        entries = []
        with self.manifest_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid manifest JSON at line {line_number}") from error
        return entries

    def record(self, entry: dict, source_path: Path, source_hash: str, text: str) -> DocumentRecord:
        nfc = lambda value: unicodedata.normalize("NFC", str(value or ""))
        base_year = nfc(entry.get("base_year"))
        base_month = entry.get("base_month")
        return DocumentRecord(
            document_id=nfc(entry.get("doc_id") or entry.get("id") or source_path.name),
            source_path=source_path,
            source_hash=source_hash,
            text=text,
            corp_name=nfc(entry.get("corp_name") or entry.get("listed_name")),
            corp_code=nfc(entry.get("corp_code")),
            document_type=nfc(entry.get("report_nm") or entry.get("doc_subtype")),
            market=nfc(entry.get("market")),
            report_period=base_year + (f"-{int(base_month):02d}" if base_month else ""),
            disclosure_date=_parse_date(entry.get("rcept_dt") or entry.get("disclosure_date")),
            source_group=nfc(entry.get("doc_group")),
            file_extension=nfc(entry.get("file_format")),
        )


class CorpusScanner:
    def __init__(self, source_root: str | Path) -> None:
        self.source_root = Path(source_root).expanduser().resolve()
        if not self.source_root.exists():
            raise FileNotFoundError(f"Corpus root does not exist: {self.source_root}")
        self.corpus_root = self.source_root / "corpus"
        self.manifest = ManifestIndex(self.corpus_root / "manifest.jsonl")

    def _files_for(self, entry: dict) -> list[Path]:
        relative = normalize_path(entry.get("file_path", ""))
        target = (self.corpus_root / relative).resolve()
        if self.corpus_root not in target.parents and target != self.corpus_root:
            raise ValueError(f"Manifest path escapes corpus root: {relative}")
        if target.is_file():
            return [target]
        if target.is_dir():
            expected = f".{normalize_path(entry.get('file_format', '')).lower().lstrip('.') }"
            return [path for path in target.rglob("*") if path.is_file() and (not expected or path.suffix.lower() == expected)]
        return []

    def scan(self, *, limit: int | None = None) -> tuple[list[DocumentRecord], list[str]]:
        records: list[DocumentRecord] = []
        errors: list[str] = []
        for entry in self.manifest.entries[:limit] if limit else self.manifest.entries:
            try:
                paths = self._files_for(entry)
                if not paths:
                    errors.append(f"{entry.get('doc_id')}: no source files")
                    continue
                text = "\n\n".join(read_document(path) for path in paths).strip()
                if not text:
                    errors.append(f"{entry.get('doc_id')}: empty text")
                    continue
                records.append(self.manifest.record(entry, paths[0], sha256_files(paths), text))
            except Exception as error:  # noqa: BLE001
                errors.append(f"{entry.get('doc_id')}: {type(error).__name__}: {error}")
        return records, errors
