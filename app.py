from fastapi import FastAPI

from application.factory import build_answer_service
from rag.config import settings
from rag.schemas import AnswerResponse


app = FastAPI(title="AI Festival RAG API", version="0.1.0")
answer_service = build_answer_service()


@app.get("/health")
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "environment": settings.environment,
        "implementation": settings.rag_backend,
    }


@app.get("/answer", response_model=AnswerResponse)
def answer(question_id: str, question: str) -> AnswerResponse:
    return answer_service.answer(question_id=question_id, question=question)
