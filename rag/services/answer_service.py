from rag.schemas import AnswerResponse, RetrievedDocument
from rag.services.clova_client import ClovaClient
from rag.services.retriever import Retriever


class AnswerService:
    def __init__(self) -> None:
        self.retriever = Retriever(documents=[])
        self.clova_client = ClovaClient()

    def answer(self, question_id: str, question: str) -> AnswerResponse:
        documents = self.retriever.search(question)
        context = self._format_context(documents)

        if not documents:
            return AnswerResponse(
                question_id=question_id,
                question=question,
                retrieved_context="",
                think_trace="검색 결과가 없습니다.",
                answer="관련 근거 문서를 찾지 못했습니다.",
            )

        return AnswerResponse(
            question_id=question_id,
            question=question,
            retrieved_context=context,
            think_trace="관련 문서를 검색했습니다. CLOVA Studio 생성 API 연결이 필요합니다.",
            answer="검색된 근거를 확인했습니다. 최종 답변 생성 API가 아직 연결되지 않았습니다.",
        )

    @staticmethod
    def _format_context(documents: list[RetrievedDocument]) -> str:
        return "\n\n".join(
            f"[출처: {document.source}]\n{document.text}" for document in documents
        )

