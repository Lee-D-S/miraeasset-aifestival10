"""Runtime factory for the canonical Stage1→Stage4 graph."""

from __future__ import annotations

import os
from pathlib import Path

import config
from integration.graph import StageNodes
from integration.clova import ClovaChatClient
from integration.readiness import (
    raise_if_invalid,
    validate_container_settings,
    validate_corpus_directory,
    validate_environment,
    validate_fixture,
    validate_sqlite_path,
)
from integration.service import StagePipeline
from stage1 import build_stage1_node
from stage2 import (
    ClovaEmbeddings,
    ClovaQueryEmbedding,
    JsonFixtureRetriever,
    LocalHybridRetriever,
    RetrievalConfig,
    build_stage2_node,
    chroma_server,
    postgres_engine,
)
from stage3 import build_stage3_node
from stage4 import build_stage4_node
from stage3.agents.answer import AnswerWriter


def _build_retriever(settings: config.Stage2Settings, *, corpus: Path | None):
    """Open the Stage2 retriever for the configured mode.

    Every path and connection string comes from :mod:`config`; nothing here
    recomputes a project-relative path or reads the environment directly.
    """

    if settings.mode == "fixture":
        # "Naver API" test DB: a JSON file of CLOVA-precomputed document
        # embeddings; queries are embedded live against the same CLOVA
        # endpoint. No SQL RDB or vector DB service required.
        retriever = JsonFixtureRetriever.from_path(
            settings.fixture_path, query_embedder=ClovaQueryEmbedding()
        )
        raise_if_invalid(validate_fixture(retriever))
        return retriever

    if settings.mode == "local":
        # Local hybrid store: a SQLite file for metadata filtering + a local
        # Chroma persist directory for vector search.
        raise_if_invalid(validate_sqlite_path(settings.sqlite_path))
        retriever = LocalHybridRetriever(
            settings.sqlite_path,
            chroma_dir=settings.chroma_path,
            collection_name=settings.chroma_collection,
            embedding_function=ClovaEmbeddings(),
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
                embedding_function=ClovaEmbeddings(),
                collection_name=settings.chroma_collection,
            ),
        )
    else:
        raise RuntimeError(
            f"unsupported Stage2 mode: {settings.mode}; "
            f"choose one of {', '.join(config.VALID_STAGE2_MODES)}"
        )

    raise_if_invalid(retriever.readiness_issues())
    if corpus is not None:
        raise_if_invalid(
            retriever.manifest_consistency_issues(corpus / "manifest.jsonl")
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
    answer_client = ClovaChatClient() if live_llm else None

    retriever = _build_retriever(settings, corpus=corpus)

    return StagePipeline(StageNodes(
        stage1=stage1,
        # Keep all metadata-filtered chunks in the small smoke corpus so
        # Stage3 can recover aggregate rows that rank below subsidiary or
        # segment rows in the hybrid score.  Retrieval prompt compaction still
        # bounds what is sent to external LLMs.
        stage2=build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=20)),
        stage3=build_stage3_node(answer_writer=AnswerWriter(answer_client)),
        stage4=build_stage4_node(validator_client=answer_client),
    ))


__all__ = ["build_pipeline"]
