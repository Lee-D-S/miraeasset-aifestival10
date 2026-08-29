"""Minimal four-stage integration boundary."""

from integration.graph import StageNodes, build_graph
from integration.service import StagePipeline

__all__ = ["StageNodes", "StagePipeline", "build_graph"]
