"""Experiment-only Stage1 to Stage4 pipeline factory."""

from __future__ import annotations

import os
from pathlib import Path

import config
from integration.clova import ClovaChatClient
from integration.graph import StageNodes
from integration.rate_limit import ClovaRateLimiter
from integration.service import StagePipeline
from integration.testing import (
    DeterministicAnswerWriter,
    DeterministicSemanticValidator,
)
from stage1 import build_stage1_node
from stage2.backends import readonly_sqlite_engine
from stage2.embedding import E5Embeddings
from stage2.node import build_stage2_node
from stage2.retrieval import RetrievalConfig
from stage2.retrieval_experiments import (
    EXPERIMENT_PROFILES,
    build_experiment_retriever,
    resolve_index_paths,
)
from stage3 import build_stage3_node
from stage3.agents.answer import AnswerWriter
from stage4 import build_stage4_node


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 1)
    except ValueError:
        return default


def _corpus_dir() -> Path:
    value = os.getenv("EXPERIMENT_CORPUS_DIR") or os.getenv("CORPUS_DIR")
    if not value:
        raise RuntimeError(
            "EXPERIMENT_CORPUS_DIR or CORPUS_DIR is required for the experiment service"
        )
    path = Path(value).expanduser().resolve()
    if not (path / "universe.csv").is_file() or not (path / "manifest.jsonl").is_file():
        raise RuntimeError(f"experiment corpus is incomplete: {path}")
    return path


def build_pipeline() -> StagePipeline:
    """Build one profile while leaving the production factory unchanged."""

    profile = os.getenv("EXPERIMENT_PROFILE", "hnsw-python").strip().lower()
    if profile not in EXPERIMENT_PROFILES:
        raise RuntimeError(
            f"unsupported EXPERIMENT_PROFILE={profile}; choose one of "
            f"{', '.join(EXPERIMENT_PROFILES)}"
        )
    index_root = os.getenv("EXPERIMENT_INDEX_ROOT", str(config.LOCAL_DB_DIR))
    artifact_root = os.getenv(
        "EXPERIMENT_ARTIFACT_ROOT",
        str(config.DATA_DIR / "retrieval_experiments" / "supplied"),
    )
    db_path, _ = resolve_index_paths(index_root)
    embedder = E5Embeddings()
    retriever = build_experiment_retriever(
        profile=profile,
        index_root=index_root,
        artifact_root=artifact_root,
        engine=readonly_sqlite_engine(db_path),
        query_embedder=embedder,
        collection_name=os.getenv(
            "EXPERIMENT_COLLECTION", config.CHROMA_COLLECTION
        ),
        table_name=os.getenv("EXPERIMENT_TABLE", config.SQLITE_TABLE),
        nprobe=_env_int("EXPERIMENT_NPROBE", 32),
    )
    readiness = retriever.readiness_issues()
    if readiness:
        retriever.close()
        raise RuntimeError("; ".join(readiness))

    corpus = _corpus_dir()
    stage1 = build_stage1_node(
        corpus_dir=corpus,
        config_dir=corpus / "config",
        use_llm=False,
    )
    live_llm = _env_bool("EXPERIMENT_LIVE_LLM", False)
    client = None
    if live_llm:
        limiter = ClovaRateLimiter(
            default_qpm=_env_int("CLOVA_RATE_LIMIT_QPM", 60),
            default_tpm=_env_int("CLOVA_RATE_LIMIT_TPM", 40000),
            min_interval=0.2,
        )
        client = ClovaChatClient(rate_limiter=limiter)
    answer_writer = AnswerWriter(client) if live_llm else DeterministicAnswerWriter()
    validator = client if live_llm else DeterministicSemanticValidator()
    return StagePipeline(
        StageNodes(
            stage1=stage1,
            stage2=build_stage2_node(
                retriever=retriever,
                config=RetrievalConfig(
                    candidate_limit=1000,
                    branch_limit=100,
                    final_limit=200,
                ),
            ),
            stage3=build_stage3_node(answer_writer=answer_writer),
            stage4=build_stage4_node(validator_client=validator),
        )
    )


__all__ = ["build_pipeline"]
