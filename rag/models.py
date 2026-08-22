from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


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


@dataclass(frozen=True)
class ChunkRecord:
    chunk_id: str
    document_id: str
    chunk_index: int
    text: str
    source_path: str
    embedding: list[float] | None = None
    span: list[int] = field(default_factory=list)

