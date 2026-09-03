"""E5 embedding adapters used by the supplied local and container indexes.

Two interchangeable adapters, selected by ``STAGE2_EMBEDDING``:

* ``e5`` (default) -> :class:`E5Embeddings` -- ``intfloat/multilingual-e5-large``
  (1024-dim, **non-instruct**) via :mod:`fastembed` / ONNX Runtime.  This is the
  model the supplied Chroma index was built with, so build and serve share one
  vector space.  No torch / transformers / sentence-transformers dependency.
* ``e5-instruct`` -> :class:`E5InstructEmbeddings` -- kept available for the
  instruction-prefix A/B path; needs ``sentence-transformers`` + ``torch``.

Both directions are kept open on purpose; the dispatch lives in
:func:`integration.composition._embedding_function`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from langchain_core.embeddings import Embeddings


QUERY_INSTRUCTION = (
    "Retrieve relevant passages from Korean corporate disclosure filings "
    "that directly answer the financial question."
)


def format_e5_query(question: str, *, instruction: str = QUERY_INSTRUCTION) -> str:
    """Format one query according to the multilingual-e5-instruct contract."""

    if not instruction.strip():
        raise ValueError("E5 query instruction must not be empty")
    return f"Instruct: {instruction.strip()}\nQuery: {str(question).strip()}"


class E5InstructEmbeddings(Embeddings):
    """Embedding adapter matching the supplied local index.

    The shared Chroma index was built with the Hugging Face
    ``intfloat/multilingual-e5-large-instruct`` model over raw text. Keep the
    same model and normalized output here; the index is never re-embedded at
    runtime. The model's query-only instruction is applied in embed_query;
    document text remains raw so supplied vectors keep their original contract.
    """

    MODEL_NAME = "intfloat/multilingual-e5-large-instruct"

    def __init__(
        self,
        *,
        device: str = "cpu",
        local_files_only: bool = True,
        query_instruction: str | None = QUERY_INSTRUCTION,
        model: Any | None = None,
    ):
        self.query_instruction = query_instruction
        if query_instruction is not None and not query_instruction.strip():
            raise ValueError("E5 query instruction must not be empty")
        if model is not None:
            self._model = model
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as error:  # pragma: no cover - dependency boundary
            raise RuntimeError(
                "STAGE2_EMBEDDING=e5-instruct requires sentence-transformers"
            ) from error

        self._model = SentenceTransformer(
            self.MODEL_NAME,
            device=device,
            local_files_only=local_files_only,
        )

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def embed_query(self, text: str) -> list[float]:
        query = (
            format_e5_query(text, instruction=self.query_instruction)
            if self.query_instruction is not None
            else str(text).strip()
        )
        return self._encode([query])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts)


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

        self._model = load_e5_model(cache_dir=cache_dir, threads=threads)
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
    "E5InstructEmbeddings",
    "QUERY_INSTRUCTION",
    "format_e5_query",
]
