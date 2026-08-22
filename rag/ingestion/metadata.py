import json
import unicodedata
from datetime import date
from pathlib import Path

from rag.models import DocumentRecord


def _nfc(value: object) -> str:
    return unicodedata.normalize("NFC", str(value or ""))


def _parse_date(value: object) -> date | None:
    raw = _nfc(value).replace(".", "-").replace("/", "-")
    if len(raw) == 8 and raw.isdigit():
        raw = f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


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
        return DocumentRecord(
            document_id=_nfc(entry.get("doc_id") or entry.get("id") or source_path.name),
            source_path=source_path,
            source_hash=source_hash,
            text=text,
            corp_name=_nfc(entry.get("corp_name") or entry.get("listed_name")),
            corp_code=_nfc(entry.get("corp_code")),
            document_type=_nfc(entry.get("report_nm") or entry.get("doc_subtype")),
            market=_nfc(entry.get("market")),
            report_period=_nfc(entry.get("base_year"))
            + (f"-{int(entry['base_month']):02d}" if entry.get("base_month") else ""),
            disclosure_date=_parse_date(entry.get("rcept_dt") or entry.get("disclosure_date")),
            source_group=_nfc(entry.get("doc_group")),
            file_extension=_nfc(entry.get("file_format")),
        )

