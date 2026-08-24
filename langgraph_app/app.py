from fastapi import FastAPI

from langgraph_app.service import LangGraphAnswerService
from rag.config import settings
from rag.schemas import AnswerResponse


app = FastAPI(title="AI Festival LangGraph RAG API", version="0.1.0")
answer_service = LangGraphAnswerService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment, "implementation": "langgraph"}


@app.get("/answer", response_model=AnswerResponse)
def answer(question_id: str, question: str) -> AnswerResponse:
    return answer_service.answer(question_id=question_id, question=question)

