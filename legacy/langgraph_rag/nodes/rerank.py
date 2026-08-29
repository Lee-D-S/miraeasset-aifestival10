from langgraph_rag.contracts import RerankerPort
from langgraph_rag.state import GraphState


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
                "fallback_reason": "" if result["documents"] else "검색된 문서 중 질문과 관련성이 충분한 문서를 리랭커가 선택하지 못했습니다.",
                "trace": [f"cited={len(result['documents'])}"],
            }
        except Exception as error:
            return {"cited_documents": [], "status": "error", "error": str(error), "trace": ["rerank_error"]}

    return rerank_node
