from typing import Literal


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
