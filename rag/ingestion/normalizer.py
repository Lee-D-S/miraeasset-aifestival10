import hashlib
import unicodedata
from pathlib import Path


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    lines = [" ".join(line.split()) for line in value.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def normalize_path(value: str | Path) -> str:
    return unicodedata.normalize("NFC", str(value))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda item: normalize_path(item)):
        digest.update(normalize_path(path).encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()

