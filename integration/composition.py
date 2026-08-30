"""Runtime factory for the canonical Stage1→Stage4 graph."""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in minimal environments
    load_dotenv = None

from integration.graph import StageNodes
from integration.service import StagePipeline
from stage1 import build_stage1_node
from stage2 import ClovaQueryEmbedding, JsonFixtureRetriever, RetrievalConfig, build_stage2_node
from stage3 import build_stage3_node
from stage4 import build_stage4_node


def build_pipeline() -> StagePipeline:
    """Compose the executable pipeline from environment-selected adapters."""
    if load_dotenv is not None:
        load_dotenv()
    stage1 = build_stage1_node()
    backend = os.getenv("STAGE2_BACKEND", "fixture").strip().lower()
    if backend != "fixture":
        raise RuntimeError(f"unsupported Stage2 backend: {backend}; production stores are not enabled yet")
    fixture = Path(os.getenv(
        "STAGE2_FIXTURE_PATH",
        str(Path(__file__).resolve().parents[1] / "legacy" / "test_data" / "disclosure_clova_local.json"),
    ))
    retriever = JsonFixtureRetriever.from_path(fixture, query_embedder=ClovaQueryEmbedding())
    return StagePipeline(StageNodes(
        stage1=stage1,
        stage2=build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=8)),
        stage3=build_stage3_node(),
        stage4=build_stage4_node(),
    ))


__all__ = ["build_pipeline"]
