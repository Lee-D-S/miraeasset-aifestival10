from rag.config import settings
from rag.generation.answer_generator import RagAnswerGenerator
from rag.retrieval.rerank import DocumentReranker
from rag.retrieval.vector_search import VectorRetriever
from rag.schemas import AnswerResponse, RetrievedDocument
from rag.clients.embedding import EmbeddingClient
from rag.clients.reranker import RerankerClient
from rag.clients.rag_reasoning import RagReasoningClient
from rag.storage.postgres import PostgresStore


class AnswerService:
    def __init__(self) -> None:
        self.retriever = None
        self.reranker = None
        self.generator = None
        if settings.postgres_dsn and settings.clova_api_key:
            store = PostgresStore(settings.postgres_dsn)
            self.retriever = VectorRetriever(store, EmbeddingClient(), settings.retrieval_top_k)
            self.reranker = DocumentReranker(RerankerClient())
            self.generator = RagAnswerGenerator(RagReasoningClient(), settings.max_tool_rounds)

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        if not self.retriever or not self.reranker or not self.generator:
            return AnswerResponse(
                question_id=question_id,
                question=question,
                retrieved_context="",
                think_trace="CLOVA API 키 또는 PostgreSQL 연결 설정이 없습니다.",
                answer="RAG 서버가 아직 연결되지 않았습니다.",
            )

        documents = self.retriever.search(question, limit=settings.retrieval_top_k)
        if not documents:
            return self._not_found_response(
                question_id,
                question,
                "지정된 공시 문서에서 질문과 관련된 검색 결과가 없습니다.",
            )

        reranked = self.reranker.rerank(question, documents[:settings.rerank_top_k])
        if not reranked.documents:
            return self._not_found_response(
                question_id,
                question,
                "검색된 문서가 질문과 충분히 관련되지 않아 답변을 생성하지 않았습니다.",
            )

        generated = self.generator.generate(question, lambda query: self.reranker.rerank(
            query,
            self.retriever.search(query, limit=settings.retrieval_top_k)[:settings.rerank_top_k],
        ))
        final_documents = generated.documents or reranked.documents
        context = self._format_context(final_documents)

        if not final_documents:
            return self._not_found_response(
                question_id,
                question,
                generated.think_trace or "최종 답변에 사용할 근거 문서가 없습니다.",
            )

        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace=generated.think_trace or "관련 문서를 검색하고 리랭킹했습니다.",
            answer=generated.answer,
        )

    @staticmethod
    def _not_found_response(
        question_id: str,
        question: str,
        trace: str,
    ) -> AnswerResponse:
        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context="",
            think_trace=trace,
            answer="제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.",
        )

    @staticmethod
    def _format_context(documents: list[RetrievedDocument]) -> str:
        return "\n\n".join(
            f"[출처: {document.source}]\n{document.text}" for document in documents
        )
