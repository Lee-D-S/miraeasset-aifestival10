"""Stage3 specialist agents."""

from stage3.agents.calculation import calculate_facts
from stage3.agents.comparison import compare_facts
from stage3.agents.event_linker import link_events
from stage3.agents.fact_extraction import extract_facts

__all__ = ["calculate_facts", "compare_facts", "extract_facts", "link_events"]
