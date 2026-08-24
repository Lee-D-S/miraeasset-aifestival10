from langgraph_app.contracts import RerankerPort
from langgraph_app.state import GraphState


def make_rerank_node(reranker: RerankerPort, limit: int = 10):
    def rerank_node(state: GraphState) -> dict:
        query = state.get("search_query") or state.get("question", "")
        try:
            candidates = state.get("retrieved_documents", [])[: max(1, limit)]
            result = reranker.rerank(query, candidates)
            return {
                "cited_documents": result["documents"],
                "reranker_answer": result["answer"],
                "suggested_queries": result["suggested_queries"],
                "status": "reranked",
                "trace": [f"cited={len(result['documents'])}"],
            }
        except Exception as error:
            return {"cited_documents": [], "status": "error", "error": str(error), "trace": ["rerank_error"]}

    return rerank_node
