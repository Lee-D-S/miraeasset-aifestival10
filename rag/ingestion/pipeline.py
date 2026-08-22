from pathlib import Path

from rag.ingestion.metadata import ManifestIndex
from rag.ingestion.normalizer import normalize_path, sha256_files
from rag.ingestion.readers import read_document
from rag.models import DocumentRecord


class CorpusScanner:
    """Read only the manifest-backed documents under the approved corpus root."""

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
            files = [path for path in target.rglob("*") if path.is_file()]
            return [path for path in files if not expected or path.suffix.lower() == expected]
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
            except Exception as error:  # noqa: BLE001 - corpus scan must continue
                errors.append(f"{entry.get('doc_id')}: {type(error).__name__}: {error}")
        return records, errors

