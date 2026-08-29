from pydantic import BaseModel, Field


class RetrievedDocument(BaseModel):
    id: str = ""
    source: str
    text: str
    score: float = Field(ge=0, le=1)
    metadata: dict = {}


class AnswerResponse(BaseModel):
    question_id: str
    question: str
    retrieved_context: str
    think_trace: str
    answer: str
