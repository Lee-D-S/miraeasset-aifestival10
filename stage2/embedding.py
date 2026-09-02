"""E5 embedding adapter used by the supplied local and container indexes."""

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


__all__ = ["E5InstructEmbeddings", "QUERY_INSTRUCTION", "format_e5_query"]
