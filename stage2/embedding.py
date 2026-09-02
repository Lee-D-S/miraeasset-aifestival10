"""E5 embedding adapter used by the supplied local and container indexes."""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.embeddings import Embeddings


class E5InstructEmbeddings(Embeddings):
    """Embedding adapter matching the supplied local index.

    The shared Chroma index was built with the Hugging Face
    ``intfloat/multilingual-e5-large-instruct`` model over raw text. Keep the
    same model and normalized output here; the index is never re-embedded at
    runtime.
    """

    MODEL_NAME = "intfloat/multilingual-e5-large-instruct"

    def __init__(self, *, device: str = "cpu", local_files_only: bool = True):
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
        return self._encode([text])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts)


__all__ = ["E5InstructEmbeddings"]
