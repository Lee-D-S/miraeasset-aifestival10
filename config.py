"""Project-wide path and Stage2 backend configuration.

Every module that opens a database, a vector store, a corpus, or a fixture
reads its location from here instead of recomputing
``Path(__file__).resolve().parents[...]`` or calling ``os.getenv`` directly.
This mirrors ``app/config.py`` in the reference implementation: one module
owns the project-root anchor and the mode selection.

Relative values coming from the environment are resolved against
:data:`PROJECT_ROOT`, never the current working directory, so the pipeline
behaves the same whether it is started from the repo root, from ``scripts/``
or from a container ``WORKDIR``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional in minimal environments
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv is not None:
    # Load .env once, here, so no other module has to.
    load_dotenv()


# --- Project root ---------------------------------------------------------------
# config.py lives at the repo root, so its parent *is* the project root.
PROJECT_ROOT = Path(__file__).resolve().parent


def resolve_path(value: str | os.PathLike[str] | None, default: Path) -> Path:
    """Resolve a configured path.

    An empty/unset ``value`` yields ``default``. A relative ``value`` is
    resolved against :data:`PROJECT_ROOT`; an absolute ``value`` is used
    as-is. ``~`` is expanded either way.
    """

    text = str(value).strip() if value is not None else ""
    if not text:
        return default
    path = Path(text).expanduser()
    return path if path.is_absolute() else (PROJECT_ROOT / path)


# --- Shared directories -------------------------------------------------------
DATA_DIR = resolve_path(os.getenv("DATA_DIR"), PROJECT_ROOT / "data")

# All local DB artifacts (SQLite file + Chroma persist dir) live under one
# directory, overridable with STAGE2_DB_DIR. Default keeps the historical
# ``data/local_smoke`` location.
DB_DIR = resolve_path(os.getenv("STAGE2_DB_DIR"), DATA_DIR / "local_smoke")

# Legacy CLOVA-precomputed embedding fixture ("Naver API" test DB).
FIXTURE_PATH = resolve_path(
    os.getenv("STAGE2_FIXTURE_PATH"),
    PROJECT_ROOT / "legacy" / "test_data" / "disclosure_clova_local.json",
)

# Local hybrid store: a SQLite file for metadata filtering + a Chroma
# persist directory for vector search.
SQLITE_PATH = resolve_path(os.getenv("STAGE2_INDEX_PATH"), DB_DIR / "smoke.db")
CHROMA_PATH = resolve_path(os.getenv("STAGE2_CHROMA_PATH"), DB_DIR / "smoke_chroma")

# One collection name for every vector-store mode so a local persist dir and
# a Chroma server address the same logical collection.
CHROMA_COLLECTION = os.getenv("STAGE2_CHROMA_COLLECTION", "").strip() or "stage2_chunks"

# Container store: a Dockerized Postgres RDB and a Chroma *server*.
RDB_URL = os.getenv("STAGE2_RDB_URL", "").strip()
CHROMA_HOST = os.getenv("STAGE2_CHROMA_HOST", "").strip()

try:
    CHROMA_PORT = int(os.getenv("STAGE2_CHROMA_PORT", "8000") or "8000")
except ValueError:
    CHROMA_PORT = 8000


# --- Stage2 mode -------------------------------------------------------------
VALID_STAGE2_MODES: tuple[str, ...] = ("fixture", "local", "container")
DEFAULT_STAGE2_MODE = "fixture"


def resolve_stage2_mode() -> str:
    """Return the configured Stage2 mode.

    ``STAGE2_MODE`` (``fixture`` | ``local`` | ``container``) is authoritative.
    When it is unset the legacy ``STAGE2_BACKEND`` is honoured: ``fixture`` maps
    to ``fixture``; ``sqlite`` maps to ``container`` if a Postgres DSN or a
    Chroma host is configured, otherwise ``local``.
    """

    mode = os.getenv("STAGE2_MODE", "").strip().lower()
    if mode:
        return mode

    legacy = os.getenv("STAGE2_BACKEND", "").strip().lower()
    if legacy == "sqlite":
        networked = bool(
            os.getenv("STAGE2_RDB_URL", "").strip()
            or os.getenv("STAGE2_CHROMA_HOST", "").strip()
        )
        return "container" if networked else "local"
    if legacy:
        # Unknown legacy value: pass it through so validate_environment rejects it.
        return legacy
    return DEFAULT_STAGE2_MODE


def corpus_dir() -> Path | None:
    """Explicit Stage1 corpus directory, or ``None`` for auto-discovery.

    A relative ``CORPUS_DIR`` is anchored to the project root; unset leaves
    Stage1's own filesystem search in charge.
    """

    raw = os.getenv("CORPUS_DIR", "").strip()
    return resolve_path(raw, PROJECT_ROOT) if raw else None


@dataclass(frozen=True)
class Stage2Settings:
    """Resolved Stage2 configuration for one ``build_pipeline`` call."""

    mode: str
    fixture_path: Path
    sqlite_path: Path
    chroma_path: Path
    rdb_url: str
    chroma_host: str
    chroma_port: int
    chroma_collection: str

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @classmethod
    def from_env(cls) -> "Stage2Settings":
        """Snapshot the current environment into an immutable settings object."""

        return cls(
            mode=resolve_stage2_mode(),
            fixture_path=resolve_path(
                os.getenv("STAGE2_FIXTURE_PATH"), FIXTURE_PATH
            ),
            sqlite_path=resolve_path(os.getenv("STAGE2_INDEX_PATH"), SQLITE_PATH),
            chroma_path=resolve_path(os.getenv("STAGE2_CHROMA_PATH"), CHROMA_PATH),
            rdb_url=os.getenv("STAGE2_RDB_URL", "").strip(),
            chroma_host=os.getenv("STAGE2_CHROMA_HOST", "").strip(),
            chroma_port=CHROMA_PORT,
            chroma_collection=(
                os.getenv("STAGE2_CHROMA_COLLECTION", "").strip() or CHROMA_COLLECTION
            ),
        )


__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "DB_DIR",
    "FIXTURE_PATH",
    "SQLITE_PATH",
    "CHROMA_PATH",
    "CHROMA_COLLECTION",
    "RDB_URL",
    "CHROMA_HOST",
    "CHROMA_PORT",
    "VALID_STAGE2_MODES",
    "DEFAULT_STAGE2_MODE",
    "Stage2Settings",
    "resolve_path",
    "resolve_stage2_mode",
    "corpus_dir",
]
