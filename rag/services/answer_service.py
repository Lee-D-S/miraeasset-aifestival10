from pathlib import Path

from common.config import settings
from common.fallback import format_fallback_answer
from rag.generation.answer_generator import RagAnswerGenerator
from rag.retrieval.rerank import DocumentReranker
from rag.retrieval.alternatives import AlternativeFinder
from rag.retrieval.vector_search import VectorRetriever
from common.schemas import AnswerResponse, RetrievedDocument
from rag.clients.embedding import EmbeddingClient
from rag.clients.reranker import RerankerClient
from rag.clients.rag_reasoning import RagReasoningClient
from rag.storage.local import LocalVectorStore
from rag.storage.postgres import PostgresStore


class AnswerService:
    def __init__(self) -> None:
        self.retriever = None
        self.reranker = None
        self.generator = None
        self.alternative_finder = None
        if settings.clova_api_key and settings.postgres_dsn:
            store = PostgresStore(settings.postgres_dsn)
        elif settings.clova_api_key and Path(settings.local_vector_index).exists():
            store = LocalVectorStore.load(settings.local_vector_index)
        else:
            store = None
        if store is not None:
            self.retriever = VectorRetriever(store, EmbeddingClient(), settings.retrieval_top_k)
            self.reranker = DocumentReranker(RerankerClient())
            self.generator = RagAnswerGenerator(RagReasoningClient(), settings.max_tool_rounds)
            self.alternative_finder = AlternativeFinder(self.retriever)

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        if not self.retriever or not self.reranker or not self.generator:
            return AnswerResponse(
                question_id=question_id,
                question=question,
                retrieved_context="",
                think_trace="CLOVA API 키 또는 Vector Store 연결 설정이 없습니다.",
                answer="RAG 서버가 아직 연결되지 않았습니다.",
            )

        documents = self.retriever.search(question, limit=settings.retrieval_top_k)
        if not documents:
            return self._not_found_response(
                question_id,
                question,
                "요청한 기업·기간·공시 조건과 일치하는 검색 문서가 없습니다.",
                question,
            )

        reranked = self.reranker.rerank(question, documents[:settings.rerank_top_k])
        if not reranked.documents:
            return self._not_found_response(
                question_id,
                question,
                "검색된 문서 중 질문과 관련성이 충분한 문서를 리랭커가 선택하지 못했습니다.",
                question,
            )

        generated = self.generator.generate(question, lambda query: self.reranker.rerank(
            query,
            self.retriever.search(query, limit=settings.retrieval_top_k)[:settings.rerank_top_k],
        ), initial_documents=reranked.documents)
        final_documents = generated.documents or reranked.documents
        context = self._format_context(final_documents)

        if not final_documents:
            return self._not_found_response(
                question_id,
                question,
                generated.think_trace or "최종 답변에 사용할 근거 문서가 없습니다.",
                question,
            )

        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace=generated.think_trace or "관련 문서를 검색하고 리랭킹했습니다.",
            answer=generated.answer,
        )

    def _not_found_response(
        self,
        question_id: str,
        question: str,
        trace: str,
        lookup_question: str,
    ) -> AnswerResponse:
        alternatives = self.alternative_finder.find(lookup_question) if self.alternative_finder else None
        same_company = alternatives.same_company if alternatives else []
        same_period = alternatives.same_period if alternatives else []
        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context="",
            think_trace=trace,
            answer=format_fallback_answer(
                trace,
                same_company=[document.model_dump() for document in same_company],
                same_period=[document.model_dump() for document in same_period],
            ),
        )

    @staticmethod
    def _format_context(documents: list[RetrievedDocument]) -> str:
        return "\n\n".join(
            f"[출처: {document.source}]\n{document.text}" for document in documents
        )
