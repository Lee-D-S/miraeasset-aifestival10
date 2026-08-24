from langgraph_rag.contracts import LlmPort
from langgraph_rag.state import GraphState


def make_rewrite_query_node(llm: LlmPort):
    def rewrite_query_node(state: GraphState) -> dict:
        prompt = (
            "Rewrite the following question into one concise Korean search query for a disclosure database. "
            "Return only the query.\nQuestion: " + state.get("question", "")
        )
        try:
            response = llm.generate([{"role": "user", "content": prompt}], [])
            message = response.get("message", {}) or {}
            query = str(message.get("content", "")).strip().strip('"')
        except Exception:
            query = ""
        return {
            "search_query": query or state.get("question", ""),
            "retrieved_documents": [],
            "cited_documents": [],
            "messages": [],
            "answer": "",
            "groundedness": "not_sure",
            "retry_count": state.get("retry_count", 0) + 1,
            "status": "query_rewritten",
            "trace": ["query_rewrite"],
        }

    return rewrite_query_node
