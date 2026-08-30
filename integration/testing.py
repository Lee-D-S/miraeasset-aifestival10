"""Deterministic, no-network pipeline factory for local integration tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from integration.graph import StageNodes
from integration.service import StagePipeline
from stage2 import InMemoryRetriever, RetrievalConfig, build_stage2_node
from stage3 import build_stage3_node
from stage3.agents.answer import AnswerWriter
from stage4 import build_stage4_node


class DeterministicSemanticValidator:
    """Minimal semantic client used only by local tests."""

    def generate_json(self, _messages: Sequence[Mapping[str, Any]], *, schema: Mapping[str, Any]) -> dict[str, Any]:
        del schema
        return {
            "pass": True,
            "issues": [],
            "unsupported_claims": [],
            "missing_aspects": [],
            "summary": "deterministic local validation",
        }


class DeterministicAnswerWriter(AnswerWriter):
    """Template writer that makes the citation marker explicit for Stage4."""

    def write(self, **kwargs: Any) -> tuple[str, str]:
        answer, mode = super().write(**kwargs)
        citations = kwargs.get("citations", [])
        if citations:
            answer = f"{answer}\n[source:{citations[0].get('document_id', '')}]"
        return answer, mode


def build_deterministic_pipeline(
    *,
    intent: Mapping[str, Any],
    documents: Sequence[Mapping[str, Any]],
    vector_scores: Mapping[str, float] | None = None,
    recursion_limit: int = 24,
) -> StagePipeline:
    """Build a complete local graph without CLOVA, DB, or embedding services."""

    intent_payload = dict(intent)

    def stage1(_state: Mapping[str, Any]) -> dict[str, Any]:
        return {"intent": intent_payload, "route": str(intent_payload.get("route", "ok"))}

    retriever = InMemoryRetriever(documents, vector_scores=vector_scores)
    return StagePipeline(
        StageNodes(
            stage1=stage1,
            stage2=build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=8)),
            stage3=build_stage3_node(answer_writer=DeterministicAnswerWriter()),
            stage4=build_stage4_node(validator_client=DeterministicSemanticValidator()),
        ),
        recursion_limit=recursion_limit,
    )


__all__ = ["DeterministicAnswerWriter", "DeterministicSemanticValidator", "build_deterministic_pipeline"]
