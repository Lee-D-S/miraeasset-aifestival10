from langgraph_rag.contracts import RetrieverPort
from langgraph_rag.state import GraphState


def make_retrieve_node(retriever: RetrieverPort, limit: int = 20):
    def retrieve_node(state: GraphState) -> dict:
        query = state.get("search_query") or state.get("question", "")
        try:
            documents = retriever.search(query, limit=limit)
            return {
                "retrieved_documents": documents,
                "status": "retrieved",
                "error": "",
                "fallback_reason": "" if documents else "요청한 기업·기간·공시 조건과 일치하는 검색 문서가 없습니다.",
                "trace": [f"retrieved={len(documents)}"],
            }
        except Exception as error:
            return {"retrieved_documents": [], "status": "error", "error": str(error), "trace": ["retrieve_error"]}

    return retrieve_node
