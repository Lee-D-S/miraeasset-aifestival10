from typing import Literal

from langgraph_app.state import GraphState


def route_after_retrieve(state: GraphState) -> Literal["has_documents", "no_documents"]:
    return "has_documents" if state.get("retrieved_documents") else "no_documents"


def route_after_rerank(state: GraphState) -> Literal["has_documents", "no_documents"]:
    return "has_documents" if state.get("cited_documents") else "no_documents"


def route_after_evaluation(state: GraphState) -> Literal["finalize", "rewrite_query", "retry_or_fallback"]:
    groundedness = state.get("groundedness")
    if groundedness == "grounded":
        return "finalize"
    if groundedness == "not_grounded" and state.get("retry_count", 0) < state.get("max_retries", 1):
        return "rewrite_query"
    return "retry_or_fallback"

