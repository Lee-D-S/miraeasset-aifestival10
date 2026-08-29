"""LangGraph adapter for the canonical shared AgentState."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

from .index.corpus_index import CorpusIndex
from .pipeline.build_intent import build_intent


def build_stage1_node(
    *,
    corpus_dir: Path | None = None,
    config_dir: Path | None = None,
    llm_client: Any | None = None,
    use_llm: bool | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build a Stage1 node that emits only ``intent`` and ``route`` updates."""

    index = CorpusIndex.load(corpus_dir=corpus_dir, config_dir=config_dir)

    def stage1_node(state: Mapping[str, Any]) -> dict[str, Any]:
        question = str(state.get("question", ""))
        intent = build_intent(
            question,
            index,
            use_llm=use_llm,
            llm_client=llm_client,
        )
        return {"intent": intent.to_dict(), "route": intent.route}

    return stage1_node


__all__ = ["build_stage1_node"]
