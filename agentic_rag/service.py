from __future__ import annotations

import hashlib
from typing import Any

from common.config import settings
from common.fallback import format_fallback_answer
from common.schemas import AnswerResponse
from agentic_rag.deterministic.evidence import context_text, validate_evidence
from agentic_rag.deterministic.metadata import extract_metadata
from agentic_rag.graph import build_graph
from agentic_rag.infrastructure.retrieval import LocalVectorRetriever, RetrieverAdapter
from agentic_rag.infrastructure.reranker_adapter import ClovaReranker
from agentic_rag.llm.hyperclova_client import HyperClovaClient
from agentic_rag.llm.prompts import ANSWER_PROMPT
from agentic_rag.registry import AgentRegistry, AgentSpec


class AgenticAnswerService:
    def __init__(self, *, retriever: Any | None = None, reranker: Any | None = None, generator: Any | None = None) -> None:
        self.retriever = retriever or RetrieverAdapter(LocalVectorRetriever(settings.local_vector_index))
        self.reranker = reranker or ClovaReranker()
        self.generator = generator or self._default_generator
        self.registry = AgentRegistry([
            AgentSpec("supervisor", "deterministic route coordinator", lambda state: {}, ("retrieval", "comparison", "calculation", "event_linker", "fact_extractor")),
            AgentSpec("retrieval", "direct evidence retrieval", lambda state: {}),
            AgentSpec("comparison", "company comparison", lambda state: {}),
            AgentSpec("calculation", "deterministic financial calculation", lambda state: {}),
            AgentSpec("event_linker", "event and disclosure linking", lambda state: {}),
            AgentSpec("fact_extractor", "evidence fact extraction", lambda state: {}),
        ])
        self.intent_client = HyperClovaClient() if settings.clova_api_key else None
        self.graph = build_graph(retriever=self.retriever, reranker=self.reranker, generator=self.generator, registry=self.registry, intent_client=self._intent_prompt if self.intent_client else None, retrieval_limit=settings.retrieval_top_k, rerank_limit=settings.rerank_top_k)

    def _intent_prompt(self, question: str):
        from agentic_rag.llm.prompts import INTENT_PROMPT
        return self.intent_client.generate_json(INTENT_PROMPT.format(question=question))

    @staticmethod
    def _default_reranker(_query: str, documents: list[dict]) -> list[dict]:
        return sorted(documents, key=lambda item: float(item.get("score", 0)), reverse=True)

    @staticmethod
    def _default_generator(question: str, documents: list[dict], _intent: str) -> str:
        if not settings.clova_api_key:
            return "\n".join(f"[출처: {d.get('source', '')}] {d.get('text', '')}" for d in documents[:3])
        return HyperClovaClient().generate_answer(ANSWER_PROMPT.format(question=question, context=context_text(documents)))

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        normalized = " ".join((question or "").split())
        state = {"question_id": question_id, "question": question, "metadata": extract_metadata(normalized, self.retriever.corp_names()), "trace": []}
        try:
            result = self.graph.invoke(state, config={"configurable": {"thread_id": f"{question_id}-{hashlib.sha1(question.encode()).hexdigest()[:12]}"}, "recursion_limit": 12})
        except Exception as exc:
            result = {**state, "fallback_reason": f"처리 중 오류가 발생했습니다: {exc}", "trace": ["graph_error"], "cited_documents": [], "answer": ""}
        documents = result.get("cited_documents", [])
        answer = result.get("answer", "")
        valid, reason = validate_evidence(documents, answer)
        if not valid:
            answer = format_fallback_answer(result.get("fallback_reason") or reason)
        trace = " ".join(str(item) for item in result.get("trace", []) if item)
        return AnswerResponse(question_id=question_id, question=question, retrieved_context=context_text(documents), think_trace=trace, answer=answer)
