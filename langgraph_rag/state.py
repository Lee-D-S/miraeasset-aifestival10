from typing import Any, Annotated, TypedDict


def append_values(left: list[Any], right: list[Any]) -> list[Any]:
    return [*left, *right]


class GraphState(TypedDict, total=False):
    question_id: str
    question: str
    search_query: str
    retrieved_documents: list[dict[str, Any]]
    cited_documents: list[dict[str, Any]]
    messages: list[dict[str, Any]]
    answer: str
    reranker_answer: str
    suggested_queries: list[str]
    alternative_documents: dict[str, list[dict[str, Any]]]
    fallback_reason: str
    groundedness: str
    evaluation_reason: str
    retry_count: int
    max_retries: int
    status: str
    error: str
    trace: Annotated[list[str], append_values]
