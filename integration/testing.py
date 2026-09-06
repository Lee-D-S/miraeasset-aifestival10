"""Deterministic, no-network pipeline factory for local integration tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from integration.graph import StageNodes
from integration.service import StagePipeline
from retriever import InMemoryRetriever, RetrievalConfig, build_retriever_node
from reasoner import build_reasoner_node
from reasoner.agents.answer import AnswerWriter
from validator import build_validator_node


class DeterministicSemanticValidator:
    """Minimal always-pass semantic client used only by local tests."""

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
    """Template writer that makes the citation marker explicit for Validator."""

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

    def interpreter(_state: Mapping[str, Any]) -> dict[str, Any]:
        return {"intent": intent_payload, "route": str(intent_payload.get("route", "ok"))}

    retriever = InMemoryRetriever(documents, vector_scores=vector_scores)
    return StagePipeline(
        StageNodes(
            interpreter=interpreter,
            retriever=build_retriever_node(retriever=retriever, config=RetrievalConfig(final_limit=8)),
            reasoner=build_reasoner_node(answer_writer=DeterministicAnswerWriter()),
            validator=build_validator_node(validator_client=DeterministicSemanticValidator()),
        ),
        recursion_limit=recursion_limit,
    )


__all__ = ["DeterministicAnswerWriter", "DeterministicSemanticValidator", "build_deterministic_pipeline"]
