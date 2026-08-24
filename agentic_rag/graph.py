from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agentic_rag.agents.nodes import answer_node, classify_node, normalize_node, refine_intent_node, rerank_node, retrieve_node, specialist_node, supervisor_node
from agentic_rag.contracts import AgenticState
from agentic_rag.router import route_after_classify, route_after_refine


def build_graph(*, retriever: Any, reranker: Any, generator: Any, registry: Any, intent_client: Any = None, retrieval_limit: int = 20, rerank_limit: int = 10):
    builder = StateGraph(AgenticState)
    builder.add_node("normalize", normalize_node)
    builder.add_node("classify", classify_node)
    builder.add_node("refine_intent", refine_intent_node(intent_client))
    builder.add_node("supervisor", supervisor_node(registry))
    builder.add_node("retrieve", retrieve_node(retriever, retrieval_limit))
    builder.add_node("rerank", rerank_node(reranker, rerank_limit))
    builder.add_node("specialist", specialist_node)
    builder.add_node("answer", answer_node(generator))
    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "classify")
    routes = {name: "supervisor" for name in ("lookup", "comparison", "calculation", "event_link", "fact_extraction")}
    routes["fallback"] = END
    routes["refine_intent"] = "refine_intent"
    builder.add_conditional_edges("classify", route_after_classify, routes)
    builder.add_conditional_edges("refine_intent", route_after_refine, {**{name: "supervisor" for name in ("lookup", "comparison", "calculation", "event_link", "fact_extraction")}, "fallback": END})
    builder.add_edge("supervisor", "retrieve")
    builder.add_edge("retrieve", "rerank")
    builder.add_edge("rerank", "specialist")
    builder.add_edge("specialist", "answer")
    builder.add_edge("answer", END)
    return builder.compile()
