from fastapi import FastAPI

from rag.config import settings
from rag.schemas import AnswerResponse
from rag.services.answer_service import AnswerService


app = FastAPI(title="AI Festival RAG API", version="0.1.0")
answer_service = AnswerService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.environment}


@app.get("/answer", response_model=AnswerResponse)
def answer(question_id: str, question: str) -> AnswerResponse:
    return answer_service.answer(question_id=question_id, question=question)

