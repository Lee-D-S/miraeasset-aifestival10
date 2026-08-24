from typing import Any

from langgraph.graph import END, START, StateGraph

from langgraph_rag.contracts import GraphDependencies
from langgraph_rag.nodes.evaluate_groundedness import make_evaluate_groundedness_node
from langgraph_rag.nodes.fallback import fallback_node
from langgraph_rag.nodes.finalize import finalize_node
from langgraph_rag.nodes.generate_answer import make_generate_answer_node
from langgraph_rag.nodes.rerank import make_rerank_node
from langgraph_rag.nodes.retrieve import make_retrieve_node
from langgraph_rag.nodes.rewrite_query import make_rewrite_query_node
from langgraph_rag.routes import (
    route_after_evaluation,
    route_after_retrieve,
    route_after_rerank,
)
from langgraph_rag.state import GraphState


def build_graph(
    dependencies: GraphDependencies,
    *,
    max_retries: int = 1,
    rerank_limit: int = 10,
    checkpointer: Any = None,
):
    builder = StateGraph(GraphState)
    builder.add_node("retrieve", make_retrieve_node(dependencies.retriever))
    builder.add_node("rerank", make_rerank_node(dependencies.reranker, rerank_limit))
    builder.add_node("generate_answer", make_generate_answer_node(dependencies.llm))
    builder.add_node("evaluate_groundedness", make_evaluate_groundedness_node(dependencies.llm))
    builder.add_node("rewrite_query", make_rewrite_query_node(dependencies.llm))
    builder.add_node("fallback", fallback_node)
    builder.add_node("finalize", finalize_node)

    builder.add_edge(START, "retrieve")
    builder.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {"has_documents": "rerank", "no_documents": "fallback"},
    )
    builder.add_conditional_edges(
        "rerank",
        route_after_rerank,
        {"has_documents": "generate_answer", "no_documents": "fallback"},
    )
    builder.add_edge("generate_answer", "evaluate_groundedness")
    builder.add_conditional_edges(
        "evaluate_groundedness",
        route_after_evaluation,
        {"finalize": "finalize", "rewrite_query": "rewrite_query", "retry_or_fallback": "fallback"},
    )
    builder.add_edge("rewrite_query", "retrieve")
    builder.add_edge("fallback", END)
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)


def render_mermaid(graph) -> str:
    return graph.get_graph().draw_mermaid()
