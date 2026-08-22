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
        reranked = self.reranker.rerank(question, documents[:settings.rerank_top_k])
        generated = self.generator.generate(question, lambda query: self.reranker.rerank(
            query,
            self.retriever.search(query, limit=settings.retrieval_top_k)[:settings.rerank_top_k],
        ))
        final_documents = generated.documents or reranked.documents
        context = self._format_context(final_documents)

        if not final_documents:
            return AnswerResponse(
                question_id=question_id,
                question=question,
                retrieved_context="",
                think_trace=generated.think_trace or "검색 결과가 없습니다.",
                answer="관련 근거 문서를 찾지 못했습니다.",
            )

        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace=generated.think_trace or "관련 문서를 검색하고 리랭킹했습니다.",
            answer=generated.answer,
        )

    @staticmethod
    def _format_context(documents: list[RetrievedDocument]) -> str:
        return "\n\n".join(
            f"[출처: {document.source}]\n{document.text}" for document in documents
        )
