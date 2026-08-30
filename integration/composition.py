"""Runtime factory for the canonical Stage1→Stage4 graph."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in minimal environments
    load_dotenv = None

from integration.graph import StageNodes
from integration.clova import ClovaChatClient
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


def build_pipeline() -> StagePipeline:
    """Compose the executable pipeline from environment-selected adapters."""
    if load_dotenv is not None:
        load_dotenv()
    stage1 = build_stage1_node()
    live_llm = os.getenv("CLOVA_LLM_ENABLED", "false").strip().lower() == "true"
    answer_client = ClovaChatClient() if live_llm else None
    backend = os.getenv("STAGE2_BACKEND", "fixture").strip().lower()
    if backend == "fixture":
        # Test/offline backend: the "database" is a JSON file of
        # CLOVA-precomputed document embeddings; queries are embedded live
        # against the same CLOVA embedding endpoint (stage2/embedding.py).
        # No SQL RDB or vector DB service required.
        default_fixture = Path(__file__).resolve().parents[1] / "legacy" / "test_data" / "disclosure_clova_local.json"
        fixture = Path(os.getenv("STAGE2_FIXTURE_PATH", "").strip() or str(default_fixture))
        retriever = JsonFixtureRetriever.from_path(fixture, query_embedder=ClovaQueryEmbedding())
    elif backend == "sqlite":
        # Local-first by default: a SQLite file for filtering + a Chroma
        # persist directory for vector search. Setting STAGE2_RDB_URL and/or
        # STAGE2_CHROMA_HOST swaps in a Dockerized Postgres RDB and/or a
        # Chroma server instead -- LocalHybridRetriever's SQL/vector-search
        # code does not change either way (see stage2/backends.py).
        rdb_url = os.getenv("STAGE2_RDB_URL", "").strip()
        if rdb_url:
            engine, sqlite_path = postgres_engine(rdb_url), None
        else:
            default_index = Path(__file__).resolve().parents[1] / "data" / "local_smoke" / "smoke.db"
            engine, sqlite_path = None, Path(os.getenv("STAGE2_INDEX_PATH", "").strip() or str(default_index))

        chroma_host = os.getenv("STAGE2_CHROMA_HOST", "").strip()
        if chroma_host:
            chroma_port = int(os.getenv("STAGE2_CHROMA_PORT", "8000"))
            vectorstore, chroma_dir = chroma_server(chroma_host, chroma_port, embedding_function=ClovaEmbeddings()), None
        else:
            default_chroma = Path(__file__).resolve().parents[1] / "data" / "local_smoke" / "smoke_chroma"
            vectorstore, chroma_dir = None, Path(os.getenv("STAGE2_CHROMA_PATH", "").strip() or str(default_chroma))

        retriever = LocalHybridRetriever(
            sqlite_path,
            chroma_dir=chroma_dir,
            engine=engine,
            vectorstore=vectorstore,
            embedding_function=ClovaEmbeddings(),
        )
    else:
        raise RuntimeError(f"unsupported Stage2 backend: {backend}; choose fixture or sqlite")
    return StagePipeline(StageNodes(
        stage1=stage1,
        # Keep enough lexical candidates for Stage3 to recover aggregate rows
        # that may rank below subsidiary/segment rows in the hybrid score.
        stage2=build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=20)),
        stage3=build_stage3_node(answer_writer=AnswerWriter(answer_client)),
        stage4=build_stage4_node(validator_client=answer_client),
    ))


__all__ = ["build_pipeline"]
