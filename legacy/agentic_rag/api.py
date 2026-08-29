from fastapi import FastAPI

from agentic_rag.service import AgenticAnswerService
from common.schemas import AnswerResponse

app = FastAPI(title="AI Festival Agentic RAG API", version="0.1.0")
answer_service = AgenticAnswerService()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "implementation": "agentic"}


@app.get("/answer", response_model=AnswerResponse)
def answer(question_id: str, question: str) -> AnswerResponse:
    return answer_service.answer(question_id, question)

