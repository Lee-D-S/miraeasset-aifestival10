from typing import Protocol

from common.config import settings
from common.schemas import AnswerResponse


class AnswerService(Protocol):
    def answer(self, question_id: str, question: str) -> AnswerResponse:
        ...


def build_answer_service(backend: str | None = None) -> AnswerService:
    selected = (backend or settings.rag_backend).strip().lower()
    if selected in {"classic", "rag"}:
        from rag.services.answer_service import AnswerService as ClassicAnswerService

        return ClassicAnswerService()
    if selected in {"langgraph", "langgraph_rag"}:
        from langgraph_rag.service import LangGraphAnswerService

        return LangGraphAnswerService()
    raise ValueError(
        f"Unsupported RAG_BACKEND={selected!r}. "
        "Use 'classic' or 'langgraph'."
    )
