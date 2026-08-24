from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from agentic_rag.agents.nodes import answer_node, classify_node, final_validate_node, make_parallel_retrieve_node, merge_parallel_node, normalize_node, refine_intent_node, rerank_node, retrieve_node, supervisor_node, validate_node
from agentic_rag.state import AgenticState
from agentic_rag.router import route_after_classify, route_after_refine, route_after_rerank, route_after_supervisor


def build_graph(*, retriever: Any, reranker: Any, generator: Any, registry: Any, agent_handlers: dict[str, Any], intent_client: Any = None, retrieval_limit: int = 20, rerank_limit: int = 10):
    builder = StateGraph(AgenticState)
    builder.add_node("normalize", normalize_node)
    builder.add_node("classify", classify_node)
    builder.add_node("refine_intent", refine_intent_node(intent_client))
    builder.add_node("supervisor", supervisor_node(registry))
    builder.add_node("parallel_retrieve", make_parallel_retrieve_node(retriever, retrieval_limit))
    builder.add_node("merge_parallel", merge_parallel_node)
    builder.add_node("retrieve", retrieve_node(retriever, retrieval_limit))
    builder.add_node("rerank", rerank_node(reranker, rerank_limit))
    builder.add_node("agent_retrieval", agent_handlers["retrieval"])
    builder.add_node("agent_comparison", agent_handlers["comparison"])
    builder.add_node("agent_calculation", agent_handlers["calculation"])
    builder.add_node("agent_event_linker", agent_handlers["event_linker"])
    builder.add_node("agent_fact_extractor", agent_handlers["fact_extractor"])
    builder.add_node("validate", validate_node)
    builder.add_node("answer", answer_node(generator))
    builder.add_node("final_validate", final_validate_node)
    builder.add_edge(START, "normalize")
    builder.add_edge("normalize", "classify")
    routes = {name: "supervisor" for name in ("lookup", "comparison", "calculation", "event_link", "fact_extraction")}
    routes["fallback"] = END
    routes["refine_intent"] = "refine_intent"
    builder.add_conditional_edges("classify", route_after_classify, routes)
    builder.add_conditional_edges("refine_intent", route_after_refine, {**{name: "supervisor" for name in ("lookup", "comparison", "calculation", "event_link", "fact_extraction")}, "fallback": END})
    builder.add_conditional_edges("supervisor", route_after_supervisor, {"retrieve": "retrieve"})
    builder.add_edge("parallel_retrieve", "merge_parallel")
    builder.add_edge("merge_parallel", "rerank")
    builder.add_edge("retrieve", "rerank")
    builder.add_conditional_edges("rerank", route_after_rerank, {"agent_retrieval": "agent_retrieval", "agent_comparison": "agent_comparison", "agent_calculation": "agent_calculation", "agent_event_linker": "agent_event_linker", "agent_fact_extractor": "agent_fact_extractor"})
    for node_name in ("agent_retrieval", "agent_comparison", "agent_calculation", "agent_event_linker", "agent_fact_extractor"):
        builder.add_edge(node_name, "validate")
    builder.add_edge("validate", "answer")
    builder.add_edge("answer", "final_validate")
    builder.add_edge("final_validate", END)
    return builder.compile()
