"""Process-local bounded caches shared by the canonical Retriever/Reasoner path.

The cache is an optimization layer only.  It never becomes a source of truth:
cache failures are swallowed by the safe helper functions and callers can
always run their existing deterministic calculation again.
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any, Callable


LOGGER = logging.getLogger(__name__)
DEFAULT_CACHE_TTL_SECONDS = 600.0
DEFAULT_INDEX_VERSION = "1"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 0.0)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 0)
    except (TypeError, ValueError):
        return default


def canonical_json(value: Any) -> str:
    """Serialize cache-key data deterministically without exposing secrets."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def sha256_text(value: str) -> str:
    return sha256(str(value).encode("utf-8")).hexdigest()


class TTLRUCache:
    """A small thread-safe TTL + LRU cache with defensive value copies."""

    def __init__(
        self,
        *,
        max_size: int,
        ttl_seconds: float,
        name: str = "cache",
        enabled: bool = True,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.max_size = max(int(max_size), 0)
        self.ttl_seconds = max(float(ttl_seconds), 0.0)
        self.name = str(name)
        self.enabled = bool(enabled) and self.max_size > 0
        self._clock = clock or time.monotonic
        self._items: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._evictions = 0

    def get(self, key: str) -> Any | None:
        """Return a defensive copy, or ``None`` when the key is not usable."""

        try:
            with self._lock:
                if not self.enabled:
                    self._misses += 1
                    return None
                item = self._items.get(str(key))
                if item is None:
                    self._misses += 1
                    return None
                expires_at, value = item
                if expires_at <= self._clock():
                    self._items.pop(str(key), None)
                    self._misses += 1
                    return None
                self._items.move_to_end(str(key))
                self._hits += 1
                return deepcopy(value)
        except Exception:  # noqa: BLE001 - optimization must never be fatal
            self._misses += 1
            LOGGER.debug("cache get failed: %s", self.name, exc_info=True)
            return None

    def put(self, key: str, value: Any) -> None:
        """Store a defensive copy; cache-copy failures are intentionally ignored."""

        try:
            with self._lock:
                if not self.enabled:
                    return
                cache_key = str(key)
                self._items[cache_key] = (
                    self._clock() + self.ttl_seconds,
                    deepcopy(value),
                )
                self._items.move_to_end(cache_key)
                while len(self._items) > self.max_size:
                    self._items.popitem(last=False)
                    self._evictions += 1
        except Exception:  # noqa: BLE001 - optimization must never be fatal
            LOGGER.debug("cache put failed: %s", self.name, exc_info=True)

    def clear(self) -> None:
        try:
            with self._lock:
                self._items.clear()
        except Exception:  # noqa: BLE001 - optimization must never be fatal
            LOGGER.debug("cache clear failed: %s", self.name, exc_info=True)

    def stats(self) -> dict[str, Any]:
        try:
            with self._lock:
                return {
                    "name": self.name,
                    "enabled": self.enabled,
                    "entries": len(self._items),
                    "max_size": self.max_size,
                    "ttl_seconds": self.ttl_seconds,
                    "hits": self._hits,
                    "misses": self._misses,
                    "evictions": self._evictions,
                }
        except Exception:  # noqa: BLE001 - diagnostics must never be fatal
            return {"name": self.name, "enabled": False, "entries": 0}


def safe_cache_get(cache: Any | None, key: str) -> Any | None:
    """Read an injected cache without allowing it to break the data path."""

    if cache is None:
        return None
    try:
        return cache.get(key)
    except Exception:  # noqa: BLE001 - injected cache boundary
        LOGGER.debug("cache get failed", exc_info=True)
        return None


def safe_cache_put(cache: Any | None, key: str, value: Any) -> None:
    """Write an injected cache without allowing it to break the data path."""

    if cache is None:
        return
    try:
        cache.put(key, value)
    except Exception:  # noqa: BLE001 - injected cache boundary
        LOGGER.debug("cache put failed", exc_info=True)


def _path_signature(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    candidate = Path(path)
    try:
        stat = candidate.stat()
    except OSError:
        return {"path": str(candidate), "missing": True}
    return {
        "path": str(candidate),
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "is_dir": candidate.is_dir(),
    }


def build_index_signature(
    *,
    retriever_mode: str,
    sqlite_path: str | Path | None = None,
    chroma_path: str | Path | None = None,
    backend_identity: str = "",
    index_version: str | None = None,
) -> str:
    """Build a cheap index fingerprint without hashing large index contents."""

    payload = {
        "index_version": str(
            index_version
            if index_version is not None
            else os.getenv("DIS164_CACHE_INDEX_VERSION", DEFAULT_INDEX_VERSION)
        ),
        "retriever_mode": str(retriever_mode),
        "sqlite": _path_signature(sqlite_path),
        "chroma": _path_signature(chroma_path),
        # Only a caller-provided non-secret identifier belongs here.  Raw
        # connection URLs are intentionally never accepted or logged.
        "backend_identity": str(backend_identity),
    }
    return sha256_text(canonical_json(payload))


class CacheRegistry:
    """One independent cache set for one pipeline process."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        index_signature: str = "",
        query_embedding_max: int = 256,
        structured_doc_max: int = 512,
        fact_max: int = 1024,
        candidate_max: int = 16,
    ) -> None:
        self.enabled = bool(enabled)
        self.ttl_seconds = max(float(ttl_seconds), 0.0)
        self.index_signature = str(index_signature)
        self.query_embeddings = TTLRUCache(
            max_size=query_embedding_max,
            ttl_seconds=self.ttl_seconds,
            name="query_embeddings",
            enabled=self.enabled,
        )
        self.candidate_documents = TTLRUCache(
            max_size=candidate_max,
            ttl_seconds=self.ttl_seconds,
            name="candidate_documents",
            enabled=self.enabled,
        )
        self.structured_documents = TTLRUCache(
            max_size=structured_doc_max,
            ttl_seconds=self.ttl_seconds,
            name="structured_documents",
            enabled=self.enabled,
        )
        self.fact_results = TTLRUCache(
            max_size=fact_max,
            ttl_seconds=self.ttl_seconds,
            name="fact_results",
            enabled=self.enabled,
        )

    @classmethod
    def from_env(cls, *, index_signature: str = "") -> "CacheRegistry":
        return cls(
            enabled=_env_bool("DIS164_CACHE_ENABLED", True),
            ttl_seconds=max(
                _env_float("DIS164_CACHE_TTL_SECONDS", DEFAULT_CACHE_TTL_SECONDS),
                1.0,
            ),
            index_signature=index_signature,
            query_embedding_max=_env_int("DIS164_CACHE_QUERY_EMBEDDING_MAX", 256),
            structured_doc_max=_env_int("DIS164_CACHE_STRUCTURED_DOC_MAX", 512),
            fact_max=_env_int("DIS164_CACHE_FACT_MAX", 1024),
            candidate_max=_env_int("DIS164_CACHE_CANDIDATE_MAX", 16),
        )

    def clear(self) -> None:
        for cache in self._caches():
            cache.clear()

    def stats(self) -> dict[str, Any]:
        return {cache.name: cache.stats() for cache in self._caches()}

    def _caches(self) -> tuple[TTLRUCache, ...]:
        return (
            self.query_embeddings,
            self.candidate_documents,
            self.structured_documents,
            self.fact_results,
        )


__all__ = [
    "CacheRegistry",
    "DEFAULT_CACHE_TTL_SECONDS",
    "TTLRUCache",
    "build_index_signature",
    "canonical_json",
    "safe_cache_get",
    "safe_cache_put",
    "sha256_text",
]
