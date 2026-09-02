"""Runtime factory for the canonical Stage1→Stage4 graph."""

from __future__ import annotations

import os
from pathlib import Path

import config
from integration.graph import StageNodes
from integration.clova import ClovaChatClient
from integration.rate_limit import ClovaRateLimiter
from integration.readiness import (
    PARTIAL_INDEX_ISSUES,
    raise_if_invalid,
    validate_container_settings,
    validate_corpus_directory,
    validate_environment,
    validate_sqlite_path,
)
from integration.service import StagePipeline
from stage1 import build_stage1_node
from stage2 import (
    E5InstructEmbeddings,
    LocalHybridRetriever,
    RetrievalConfig,
    build_stage2_node,
    chroma_server,
    local_chroma,
    postgres_engine,
    readonly_sqlite_engine,
)
from stage3 import build_stage3_node
from stage4 import build_stage4_node
from stage3.agents.answer import AnswerWriter


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 0)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 0.0)
    except ValueError:
        return default


def _shared_clova_rate_limiter() -> ClovaRateLimiter:
    """Share one conservative process-local budget across all CLOVA APIs."""

    return ClovaRateLimiter(
        default_qpm=_env_int("CLOVA_RATE_LIMIT_QPM", 60),
        default_tpm=_env_int("CLOVA_RATE_LIMIT_TPM", 40000),
        min_interval=max(_env_float("CLOVA_CHAT_MIN_INTERVAL", 0.2), 0.2),
    )


def _embedding_function(settings: config.Stage2Settings):
    if settings.embedding != "e5-instruct":
        raise RuntimeError(
            "unsupported Stage2 embedding: "
            f"{settings.embedding}; choose e5-instruct"
        )
    return E5InstructEmbeddings()


def _build_retriever(
    settings: config.Stage2Settings,
    *,
    corpus: Path | None,
):
    """Open the Stage2 retriever for the configured mode.

    Every path and connection string comes from :mod:`config`; nothing here
    recomputes a project-relative path or reads the environment directly.
    """

    if settings.mode == "local":
        # Local hybrid store: a SQLite file for metadata filtering + a local
        # Chroma persist directory for vector search. Both are opened without
        # creating or writing index files.
        raise_if_invalid(validate_sqlite_path(settings.sqlite_path))
        retriever = LocalHybridRetriever(
            engine=readonly_sqlite_engine(settings.sqlite_path),
            vectorstore=local_chroma(
                settings.chroma_path,
                embedding_function=_embedding_function(settings),
                collection_name=settings.chroma_collection,
                create_directory=False,
            ),
            collection_name=settings.chroma_collection,
            table_name=settings.sqlite_table,
            read_only=True,
        )
    elif settings.mode == "container":
        # Dockerized Postgres RDB + Chroma server. LocalHybridRetriever's
        # SQL/vector-search code is identical to local mode; only the
        # connections differ (see stage2/backends.py).
        raise_if_invalid(validate_container_settings(settings))
        retriever = LocalHybridRetriever(
            engine=postgres_engine(settings.rdb_url),
            vectorstore=chroma_server(
                settings.chroma_host,
                settings.chroma_port,
                embedding_function=_embedding_function(settings),
                collection_name=settings.chroma_collection,
            ),
            table_name=settings.sqlite_table,
            read_only=True,
        )
    else:
        raise RuntimeError(
            f"unsupported Stage2 mode: {settings.mode}; "
            f"choose one of {', '.join(config.VALID_STAGE2_MODES)}"
        )

    tolerated = PARTIAL_INDEX_ISSUES if settings.allow_partial_index else frozenset()
    raise_if_invalid(retriever.readiness_issues(), tolerate=tolerated)
    if corpus is not None:
        raise_if_invalid(
            retriever.manifest_consistency_issues(corpus / "manifest.jsonl"),
            tolerate=tolerated,
        )
    return retriever


def build_pipeline() -> StagePipeline:
    """Compose the executable pipeline from environment-selected adapters."""

    settings = config.Stage2Settings.from_env()
    raise_if_invalid(validate_environment(settings.mode))

    corpus = config.corpus_dir()
    if corpus is not None:
        raise_if_invalid(validate_corpus_directory(corpus))

    stage1 = build_stage1_node(corpus_dir=corpus)
    live_llm = os.getenv("CLOVA_LLM_ENABLED", "false").strip().lower() == "true"
    clova_rate_limiter = _shared_clova_rate_limiter()
    answer_client = ClovaChatClient(rate_limiter=clova_rate_limiter) if live_llm else None

    retriever = _build_retriever(settings, corpus=corpus)

    return StagePipeline(StageNodes(
        stage1=stage1,
        # Keep all metadata-filtered chunks available so Stage3 can recover
        # aggregate rows that rank below subsidiary or segment rows in the
        # hybrid score. Retrieval prompt compaction still bounds what is sent
        # to external LLMs.
        stage2=build_stage2_node(
            retriever=retriever,
            config=RetrievalConfig(
                candidate_limit=1000,
                branch_limit=100,
                final_limit=200,
            ),
        ),
        stage3=build_stage3_node(answer_writer=AnswerWriter(answer_client)),
        stage4=build_stage4_node(validator_client=answer_client),
    ))


__all__ = ["build_pipeline"]
