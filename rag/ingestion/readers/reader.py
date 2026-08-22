import json
import re
import xml.etree.ElementTree as element_tree
from html import unescape
from pathlib import Path

from rag.ingestion.normalizer import normalize_text


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
        # Some supplied disclosures contain invalid XML control characters.
        # Remove only characters forbidden by XML 1.0 and retry with stdlib.
        raw = path.read_bytes()
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", raw)
        try:
            root = element_tree.fromstring(cleaned)
            return "\n".join(text for text in root.itertext() if text and text.strip())
        except element_tree.ParseError:
            # A few filings are structurally malformed. Preserve their text by
            # removing tags rather than discarding the whole disclosure.
            return re.sub(rb"<[^>]*>", b"\n", cleaned).decode("utf-8", errors="replace")


def _read_html(path: Path) -> str:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "html.parser")
    return soup.get_text("\n")


def _read_pdf(path: Path) -> str:
    from pypdf import PdfReader

    return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)


def read_document(path: Path) -> str:
    extension = path.suffix.lower()
    if extension == ".json":
        text = _read_json(path)
    elif extension == ".xml":
        text = _read_xml(path)
    elif extension in {".html", ".htm"}:
        text = _read_html(path)
    elif extension == ".pdf":
        text = _read_pdf(path)
    elif extension in {".md", ".txt"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return normalize_text(unescape(text))
