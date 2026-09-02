"""Local e5 embedder for the DART ingestion path — via fastembed (no torch).

``intfloat/multilingual-e5-large`` (1024-dim, matching Stage2's dimension
checks), run through :mod:`fastembed` / ONNX Runtime.  fastembed is used
instead of sentence-transformers / HuggingFace because it has no torch,
transformers or Pillow dependency, so it installs cleanly on Colab and on
the NCP server alike.

fastembed applies e5's ``passage:`` / ``query:`` prefixes itself (``embed`` vs
``query_embed``); this module only adds L2 normalization so cosine similarity
in Chroma behaves.  Exposed as a factory so importing
``stage2.ingestion.dart`` never loads the model.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any

MODEL_NAME = "intfloat/multilingual-e5-large"
EMBEDDING_DIM = 1024


def l2_normalize(vector: Sequence[float]) -> list[float]:
    values = [float(component) for component in vector]
    norm = math.sqrt(sum(component * component for component in values)) or 1.0
    return [component / norm for component in values]


def load_e5_model(*, cache_dir: str | None = None, threads: int | None = None) -> Any:
    """Return a ``fastembed.TextEmbedding`` for :data:`MODEL_NAME`."""

    from fastembed import TextEmbedding

    kwargs: dict[str, Any] = {}
    if cache_dir is not None:
        kwargs["cache_dir"] = cache_dir
    if threads is not None:
        kwargs["threads"] = threads
    return TextEmbedding(MODEL_NAME, **kwargs)


__all__ = ["EMBEDDING_DIM", "MODEL_NAME", "l2_normalize", "load_e5_model"]
