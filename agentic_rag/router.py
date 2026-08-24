from typing import Literal
from langgraph.constants import Send


def route_after_classify(state: dict) -> Literal["fallback", "refine_intent", "lookup", "comparison", "calculation", "event_link", "fact_extraction"]:
    if state.get("fallback_reason") or state.get("intent") == "unsupported":
        return "fallback"
    if float(state.get("intent_confidence", 0.0)) < 0.8:
        return "refine_intent"
    return state.get("intent", "fact_extraction")


def route_after_refine(state: dict) -> Literal["fallback", "lookup", "comparison", "calculation", "event_link", "fact_extraction"]:
    if state.get("intent") == "unsupported":
        return "fallback"
    return state.get("intent", "fact_extraction")


def route_after_rerank(state: dict) -> str:
    return {
        "lookup": "agent_retrieval",
        "comparison": "agent_comparison",
        "calculation": "agent_calculation",
        "event_link": "agent_event_linker",
        "fact_extraction": "agent_fact_extractor",
    }.get(state.get("intent", "fact_extraction"), "agent_fact_extractor")


def route_after_supervisor(state: dict):
    targets = state.get("metadata", {}).get("comparison_targets", [])
    if state.get("intent") == "comparison" and len(targets) > 1:
        return [Send("parallel_retrieve", {**state, "comparison_target": target, "parallel_documents": []}) for target in sorted(targets)]
    return "retrieve"
