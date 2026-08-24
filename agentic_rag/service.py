from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from typing import Any

from common.config import settings
from common.fallback import format_fallback_answer
from common.schemas import AnswerResponse
from agentic_rag.agents.answer_generator import make_answer_generator
from agentic_rag.agents.calculation import make_calculation_agent
from agentic_rag.agents.comparison import comparison_agent
from agentic_rag.agents.event_linker import make_event_linker_agent
from agentic_rag.agents.fact_extractor import make_fact_extractor_agent
from agentic_rag.agents.retrieval import make_retrieval_agent
from agentic_rag.deterministic.evidence import context_text, validate_evidence
from agentic_rag.deterministic.metadata import extract_metadata
from agentic_rag.graph import build_graph
from agentic_rag.infrastructure.retrieval import LocalVectorRetriever, RetrieverAdapter
from agentic_rag.infrastructure.reranker_adapter import ClovaReranker
from agentic_rag.infrastructure.embedding_adapter import ClovaEmbedding
from agentic_rag.infrastructure.postgres import PostgresVectorRetriever
from agentic_rag.llm.chat_clova_x import ChatClovaXClient
from agentic_rag.llm.prompts import ANSWER_PROMPT
from agentic_rag.llm.rag_reasoning_client import RagReasoningClient
from agentic_rag.llm.model_profiles import DEFAULT_PROFILE
from agentic_rag.llm.schemas import INTENT_SCHEMA
from agentic_rag.registry import AgentRegistry, AgentSpec


class AgenticAnswerService:
    REQUEST_TIMEOUT_SECONDS = 300
    MAX_HANDOFFS = 4
    def __init__(self, *, retriever: Any | None = None, reranker: Any | None = None, generator: Any | None = None, rag_reasoning: Any | None = None) -> None:
        if retriever is not None:
            self.retriever = retriever
        elif settings.postgres_dsn:
            self.retriever = RetrieverAdapter(PostgresVectorRetriever(settings.postgres_dsn, ClovaEmbedding()))
        else:
            self.retriever = RetrieverAdapter(LocalVectorRetriever(settings.local_vector_index))
        self.reranker = reranker or ClovaReranker()
        self.chat_client = ChatClovaXClient(api_host=settings.clova_api_host, api_key=settings.clova_api_key) if settings.clova_api_key else None
        self.rag_reasoning = rag_reasoning or (RagReasoningClient(api_host=settings.clova_api_host, api_key=settings.clova_api_key) if settings.clova_api_key else None)
        search_tool = lambda query: self.retriever.search(query, settings.retrieval_top_k, {})
        self.generator = generator or make_answer_generator(self.chat_client, rag_reasoning=self.rag_reasoning, search_tool=search_tool)
        retrieval_agent = make_retrieval_agent(self.retriever, settings.retrieval_top_k)
        calculation_handler = make_calculation_agent(self.chat_client)
        self.agent_handlers = {"retrieval": retrieval_agent, "comparison": comparison_agent, "calculation": calculation_handler, "event_linker": make_event_linker_agent(self.chat_client), "fact_extractor": make_fact_extractor_agent(self.chat_client)}
        self.registry = AgentRegistry([
            AgentSpec("supervisor", "deterministic route coordinator", lambda state: {}, ("retrieval", "comparison", "calculation", "event_linker", "fact_extractor")),
            AgentSpec("retrieval", "direct evidence retrieval", retrieval_agent),
            AgentSpec("comparison", "company comparison", comparison_agent),
            AgentSpec("calculation", "deterministic financial calculation", calculation_handler),
            AgentSpec("event_linker", "event and disclosure linking", self.agent_handlers["event_linker"]),
            AgentSpec("fact_extractor", "evidence fact extraction", self.agent_handlers["fact_extractor"]),
        ])
        self.graph = build_graph(retriever=self.retriever, reranker=self.reranker, generator=self.generator, registry=self.registry, agent_handlers=self.agent_handlers, intent_client=self._intent_prompt if self.chat_client else None, retrieval_limit=settings.retrieval_top_k, rerank_limit=settings.rerank_top_k)

    def _intent_prompt(self, question: str):
        from agentic_rag.llm.prompts import INTENT_PROMPT
        return self.chat_client.generate_json([{"role": "user", "content": INTENT_PROMPT.format(question=question)}], schema=INTENT_SCHEMA, profile=DEFAULT_PROFILE)

    @staticmethod
    def _default_reranker(_query: str, documents: list[dict]) -> list[dict]:
        return sorted(documents, key=lambda item: float(item.get("score", 0)), reverse=True)

    @staticmethod
    def _default_generator(question: str, documents: list[dict], _intent: str) -> str:
        if not settings.clova_api_key:
            return "\n".join(f"[출처: {d.get('source', '')}] {d.get('text', '')}" for d in documents[:3])
        return ChatClovaXClient(api_host=settings.clova_api_host, api_key=settings.clova_api_key).generate_text([{"role": "user", "content": ANSWER_PROMPT.format(question=question, context=context_text(documents))}], profile=DEFAULT_PROFILE)

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        normalized = " ".join((question or "").split())
        state = {"question_id": question_id, "question": question, "metadata": extract_metadata(normalized, self.retriever.corp_names()), "trace": [], "agent_results": [], "provenance": [], "handoffs": [], "messages": [], "cited_documents": []}
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self.graph.invoke, state, {"configurable": {"thread_id": f"{question_id}-{hashlib.sha1(question.encode()).hexdigest()[:12]}"}, "recursion_limit": 12})
        try:
            result = future.result(timeout=self.REQUEST_TIMEOUT_SECONDS)
            if len(result.get("handoffs", [])) > self.MAX_HANDOFFS:
                raise RuntimeError("handoff limit exceeded")
        except TimeoutError:
            future.cancel()
            result = {**state, "fallback_reason": "요청 처리 시간이 제한 시간을 초과했습니다.", "trace": ["request_timeout"], "cited_documents": [], "answer": ""}
        except Exception as exc:
            result = {**state, "fallback_reason": f"처리 중 오류가 발생했습니다: {exc}", "trace": ["graph_error"], "cited_documents": [], "answer": ""}
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        documents = result.get("cited_documents", [])
        answer = result.get("answer", "")
        valid, reason = validate_evidence(documents, answer)
        valid = valid and bool(result.get("validation", {}).get("valid", False))
        if not valid:
            answer = format_fallback_answer(result.get("fallback_reason") or reason)
        trace = " ".join(str(item) for item in result.get("trace", []) if item)
        return AnswerResponse(question_id=question_id, question=question, retrieved_context=context_text(documents), think_trace=trace, answer=answer)
