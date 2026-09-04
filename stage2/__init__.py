"""Stage2 retrieval-only implementation."""

from stage2.contracts import ChunkRow, PROMOTED_COLUMNS
from stage2.node import build_stage2_node
from stage2.retrieval import InMemoryRetriever, RetrievalConfig, Stage2Retriever
from stage2.embedding import (
    E5Embeddings,
)
from stage2.local_store import LocalHybridRetriever
from stage2.backends import (
    local_chroma,
    local_sqlite_engine,
    readonly_sqlite_engine,
)
from stage2.retrieval_experiments import (
    EXPERIMENT_PROFILES,
    ExactVectorBackend,
    ExperimentHybridRetriever,
    FaissVectorBackend,
    Fts5KeywordBackend,
    HnswVectorBackend,
    PythonTokenKeywordBackend,
    build_experiment_retriever,
    build_fts5_sidecar,
    build_vector_sidecars,
)

__all__ = [
    "ChunkRow",
    "PROMOTED_COLUMNS",
    "InMemoryRetriever",
    "RetrievalConfig",
    "Stage2Retriever",
    "build_stage2_node",
    "E5Embeddings",
    "LocalHybridRetriever",
    "local_chroma",
    "local_sqlite_engine",
    "readonly_sqlite_engine",
    "EXPERIMENT_PROFILES",
    "ExactVectorBackend",
    "ExperimentHybridRetriever",
    "FaissVectorBackend",
    "Fts5KeywordBackend",
    "HnswVectorBackend",
    "PythonTokenKeywordBackend",
    "build_experiment_retriever",
    "build_fts5_sidecar",
    "build_vector_sidecars",
]
