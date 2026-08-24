from __future__ import annotations

INTENT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["lookup", "comparison", "calculation", "event_link", "fact_extraction", "unsupported"]},
        "confidence": {"type": "number"},
        "metadata": {"type": "object"},
    },
    "required": ["intent", "confidence"],
}

EVENT_LINK_SCHEMA = {
    "type": "object",
    "properties": {"events": {"type": "array"}},
    "required": ["events"],
}

FACT_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {"facts": {"type": "array"}},
    "required": ["facts"],
}
