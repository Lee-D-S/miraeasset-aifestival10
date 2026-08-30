"""LangGraph boundary for the retrieval-only Stage2."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from stage2.retrieval import RetrievalConfig, Stage2Retriever, build_search_query, retrieve


def build_stage2_node(
    *,
    retriever: Stage2Retriever,
    config: RetrievalConfig = RetrievalConfig(),
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build a node that writes only Stage2-owned State fields."""

    def stage2_node(state: Mapping[str, Any]) -> dict[str, Any]:
        query = (
            str(state.get("search_query"))
            if state.get("search_query") and state.get("search_query") != state.get("original_question", state.get("question"))
            else build_search_query(
                str(state.get("question", "")),
                state.get("intent") if isinstance(state.get("intent"), Mapping) else {},
            )
        )
        result = retrieve(
            question_id=str(state.get("question_id", "")),
            question=str(state.get("question", "")),
            intent=state.get("intent") if isinstance(state.get("intent"), Mapping) else {},
            route=str(state.get("route", "unanswerable")),
            retriever=retriever,
            config=config,
            search_query=query,
        )
        return {
            "stage2_result": result,
            "documents": result["cited_documents"],
            "retry_num": int(state.get("retry_num", 0) or 0),
            "search_attempts": int(state.get("search_attempts", 0) or 0) + 1,
            "search_query": query,
        }

    return stage2_node


__all__ = ["build_stage2_node"]
