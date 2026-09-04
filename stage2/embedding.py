"""The single active E5 embedding adapter for the supplied indexes.

``intfloat/multilingual-e5-large`` (1024 dimensions) is used for both document
and query embeddings through :mod:`fastembed` / ONNX Runtime.  The supplied
Chroma vectors are kept in this same space; no instruct-prefixed adapter is
part of the active runtime.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.embeddings import Embeddings


class E5Embeddings(Embeddings):
    """``intfloat/multilingual-e5-large`` (1024-dim, non-instruct) via fastembed.

    The active default (``STAGE2_EMBEDDING=e5``).  The same model that embedded
    the documents into ``chunk_index``'s Chroma collection also embeds the
    query.  fastembed (ONNX Runtime) is used rather than sentence-transformers
    so there is no torch / transformers / Pillow dependency; it installs
    cleanly on Colab and on the NCP server alike.  The model loads on
    construction, not on import.  fastembed applies e5's ``passage:`` /
    ``query:`` prefixes itself; this adapter only L2-normalizes so Chroma's
    cosine space behaves.
    """

    # Kept in sync with stage2.ingestion.dart.embeddings.MODEL_NAME (asserted in
    # tests/test_embedding.py) so build and serve cannot drift apart.
    MODEL_NAME = "intfloat/multilingual-e5-large"

    def __init__(self, *, cache_dir: str | None = None, threads: int | None = None):
        import os

        from stage2.ingestion.dart.embeddings import l2_normalize, load_e5_model

        # In the container image the ONNX weights are baked at a fixed path so
        # nothing is fetched at runtime (the eval network may block egress).
        cache_dir = cache_dir or os.getenv("FASTEMBED_CACHE_DIR") or None
        if threads is None:
            env_threads = os.getenv("FASTEMBED_THREADS", "").strip()
            threads = int(env_threads) if env_threads.isdigit() else None

        try:
            self._model = load_e5_model(cache_dir=cache_dir, threads=threads)
        except Exception as error:  # noqa: BLE001 - readiness boundary
            raise RuntimeError(
                "E5 embedding model cache is unavailable; preload "
                "intfloat/multilingual-e5-large before starting the server"
            ) from error
        self._normalize = l2_normalize

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            self._normalize(vector)
            for vector in self._model.embed([str(text) for text in texts])
        ]

    def embed_query(self, text: str) -> list[float]:
        vector = next(iter(self._model.query_embed([str(text)])))
        return self._normalize(vector)


__all__ = [
    "E5Embeddings",
]
