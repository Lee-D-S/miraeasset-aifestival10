import hashlib
from langgraph_rag.adapters import build_dependencies
from langgraph_rag.checkpoint import build_memory_checkpointer
from langgraph_rag.graph import build_graph
from common.config import settings
from common.fallback import format_fallback_answer
from common.schemas import AnswerResponse


class LangGraphAnswerService:
    def __init__(self) -> None:
        dependencies = build_dependencies(settings)
        self.graph = (
            build_graph(
                dependencies,
                max_retries=1,
                rerank_limit=settings.rerank_top_k,
                checkpointer=build_memory_checkpointer(),
            )
            if dependencies
            else None
        )

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        if self.graph is None:
            return self._response(
                question_id,
                question,
                "CLOVA API 키 또는 Vector Store 연결 설정이 없습니다.",
                "RAG 서버가 아직 연결되지 않았습니다.",
            )

        config = {
            "configurable": {
                "thread_id": f"{question_id}-{hashlib.sha1(question.encode('utf-8')).hexdigest()[:12]}"
            },
            "recursion_limit": 20,
        }
        result = self.graph.invoke({
            "question_id": question_id,
            "question": question,
            "retry_count": 0,
            "max_retries": 1,
            "messages": [],
            "cited_documents": [],
            "trace": [],
        }, config=config)
        documents = result.get("cited_documents", [])
        context = "\n\n".join(
            f"[출처: {document.get('source', '')}]\n{document.get('text', '')}"
            for document in documents
        )
        answer = result.get("answer", "")
        if not answer:
            answer = format_fallback_answer(
                result.get("fallback_reason")
                or result.get("evaluation_reason")
                or "답변 생성 결과가 비어 있습니다."
            )
        return self._response(
            question_id,
            question,
            " ".join(str(item) for item in result.get("trace", []) if item),
            answer,
            context=context,
        )

    @staticmethod
    def _response(question_id: str, question: str, trace: str, answer: str, *, context: str = "") -> AnswerResponse:
        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace=trace,
            answer=answer,
        )
