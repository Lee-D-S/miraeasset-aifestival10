from langgraph_app.state import GraphState


def finalize_node(state: GraphState) -> dict:
    return {"status": "completed", "trace": ["finalized"]}

