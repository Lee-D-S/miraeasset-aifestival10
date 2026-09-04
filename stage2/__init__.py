"""Stage2 retrieval-only implementation."""

from stage2.contracts import ChunkRow, PROMOTED_COLUMNS
from stage2.node import build_stage2_node
from stage2.retrieval import InMemoryRetriever, RetrievalConfig, Stage2Retriever
from stage2.embedding import (
    E5Embeddings,
    E5InstructEmbeddings,
    QUERY_INSTRUCTION,
    format_e5_query,
)
from stage2.local_store import LocalHybridRetriever
from stage2.backends import (
    chroma_server,
    local_chroma,
    local_sqlite_engine,
    postgres_engine,
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
    "E5InstructEmbeddings",
    "QUERY_INSTRUCTION",
    "format_e5_query",
    "LocalHybridRetriever",
    "chroma_server",
    "local_chroma",
    "local_sqlite_engine",
    "postgres_engine",
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
