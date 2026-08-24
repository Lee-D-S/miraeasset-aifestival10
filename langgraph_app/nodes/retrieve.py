from langgraph_app.contracts import RetrieverPort
from langgraph_app.state import GraphState


def make_retrieve_node(retriever: RetrieverPort):
    def retrieve_node(state: GraphState) -> dict:
        query = state.get("search_query") or state.get("question", "")
        try:
            documents = retriever.search(query, limit=20)
            return {"retrieved_documents": documents, "status": "retrieved", "error": "", "trace": [f"retrieved={len(documents)}"]}
        except Exception as error:
            return {"retrieved_documents": [], "status": "error", "error": str(error), "trace": ["retrieve_error"]}

    return retrieve_node

