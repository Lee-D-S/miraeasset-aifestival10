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
from stage2 import ClovaQueryEmbedding, JsonFixtureRetriever, RetrievalConfig, SQLiteStage2Repository, build_stage2_node
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
        default_fixture = Path(__file__).resolve().parents[1] / "legacy" / "test_data" / "disclosure_clova_local.json"
        fixture = Path(os.getenv("STAGE2_FIXTURE_PATH", "").strip() or str(default_fixture))
        retriever = JsonFixtureRetriever.from_path(fixture, query_embedder=ClovaQueryEmbedding())
    elif backend == "sqlite":
        default_index = Path(__file__).resolve().parents[1] / "data" / "local_smoke" / "smoke.db"
        index_path = Path(os.getenv("STAGE2_INDEX_PATH", "").strip() or str(default_index))
        retriever = SQLiteStage2Repository(index_path, query_embedder=ClovaQueryEmbedding())
    else:
        raise RuntimeError(f"unsupported Stage2 backend: {backend}; choose fixture or sqlite")
    return StagePipeline(StageNodes(
        stage1=stage1,
        stage2=build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=8)),
        stage3=build_stage3_node(answer_writer=AnswerWriter(answer_client)),
        stage4=build_stage4_node(validator_client=answer_client),
    ))


__all__ = ["build_pipeline"]
