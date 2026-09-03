"""Project-wide path and Stage2 backend configuration.

Every module that opens a database, a vector store, or a corpus reads its
location from here instead of recomputing
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

# Local hybrid store: a SQLite file for metadata filtering + a Chroma
# persist directory for vector search.
LOCAL_DB_DIR = DATA_DIR / "team-feature2-local-db" / "local_db"
SQLITE_PATH = resolve_path(
    os.getenv("STAGE2_INDEX_PATH"), LOCAL_DB_DIR / "chunk_index.db"
)
CHROMA_PATH = resolve_path(
    os.getenv("STAGE2_CHROMA_PATH"), LOCAL_DB_DIR / "chunk_index_chroma"
)

# One collection name for every vector-store mode so a local persist dir and
# a Chroma server address the same logical collection.
CHROMA_COLLECTION = os.getenv("STAGE2_CHROMA_COLLECTION", "").strip() or "chunk_vectors"
SQLITE_TABLE = os.getenv("STAGE2_SQL_TABLE", "").strip() or "chunk_index"
# Canonical default for unset ``STAGE2_EMBEDDING``. Keep this a module constant
# rather than reading ``os.getenv`` at import time so CI legacy ``e5`` env vars
# do not freeze ``config.EMBEDDING`` before tests clear the environment.
DEFAULT_STAGE2_EMBEDDING = "e5"
EMBEDDING = DEFAULT_STAGE2_EMBEDDING

# Container store: a Dockerized Postgres RDB and a Chroma *server*.
RDB_URL = os.getenv("STAGE2_RDB_URL", "").strip()
CHROMA_HOST = os.getenv("STAGE2_CHROMA_HOST", "").strip()

try:
    CHROMA_PORT = int(os.getenv("STAGE2_CHROMA_PORT", "8000") or "8000")
except ValueError:
    CHROMA_PORT = 8000


# --- Stage2 mode -------------------------------------------------------------
VALID_STAGE2_MODES: tuple[str, ...] = ("local", "container")
# ``e5`` (default, fastembed / non-instruct -- the model the supplied index was
# built with) and ``e5-instruct`` (sentence-transformers, A/B path) are both
# kept selectable; do not collapse this to a single value.
VALID_STAGE2_EMBEDDINGS: tuple[str, ...] = ("e5", "e5-instruct")
VALID_STAGE2_SQL_TABLES: tuple[str, ...] = ("chunk_index", "chunks")
DEFAULT_STAGE2_MODE = "local"


def resolve_stage2_mode() -> str:
    """Return the configured Stage2 mode.

    ``STAGE2_MODE`` (``local`` | ``container``) is the only backend selector.
    Leaving it unset selects the supplied local read-only index.
    """

    mode = os.getenv("STAGE2_MODE", "").strip().lower()
    return mode or DEFAULT_STAGE2_MODE


def allow_partial_index() -> bool:
    """Return whether expected partial-index consistency issues are tolerated."""

    return os.getenv("STAGE2_ALLOW_PARTIAL_INDEX", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


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
    sqlite_path: Path
    chroma_path: Path
    rdb_url: str
    chroma_host: str
    chroma_port: int
    chroma_collection: str
    sqlite_table: str
    embedding: str
    allow_partial_index: bool

    @property
    def sqlite_url(self) -> str:
        return f"sqlite:///{self.sqlite_path}"

    @classmethod
    def from_env(cls) -> "Stage2Settings":
        """Snapshot the current environment into an immutable settings object."""

        return cls(
            mode=resolve_stage2_mode(),
            sqlite_path=resolve_path(os.getenv("STAGE2_INDEX_PATH"), SQLITE_PATH),
            chroma_path=resolve_path(os.getenv("STAGE2_CHROMA_PATH"), CHROMA_PATH),
            rdb_url=os.getenv("STAGE2_RDB_URL", "").strip(),
            chroma_host=os.getenv("STAGE2_CHROMA_HOST", "").strip(),
            chroma_port=CHROMA_PORT,
            chroma_collection=(
                os.getenv("STAGE2_CHROMA_COLLECTION", "").strip() or CHROMA_COLLECTION
            ),
            sqlite_table=os.getenv("STAGE2_SQL_TABLE", "").strip() or SQLITE_TABLE,
            embedding=os.getenv("STAGE2_EMBEDDING", "").strip().lower()
            or DEFAULT_STAGE2_EMBEDDING,
            allow_partial_index=allow_partial_index(),
        )


__all__ = [
    "PROJECT_ROOT",
    "DATA_DIR",
    "LOCAL_DB_DIR",
    "SQLITE_PATH",
    "CHROMA_PATH",
    "CHROMA_COLLECTION",
    "SQLITE_TABLE",
    "EMBEDDING",
    "RDB_URL",
    "CHROMA_HOST",
    "CHROMA_PORT",
    "VALID_STAGE2_MODES",
    "VALID_STAGE2_EMBEDDINGS",
    "VALID_STAGE2_SQL_TABLES",
    "DEFAULT_STAGE2_MODE",
    "DEFAULT_STAGE2_EMBEDDING",
    "Stage2Settings",
    "resolve_path",
    "resolve_stage2_mode",
    "allow_partial_index",
    "corpus_dir",
]
