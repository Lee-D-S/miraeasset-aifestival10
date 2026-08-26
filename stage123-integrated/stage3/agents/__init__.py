"""Stage3 specialist agents."""

from stage3.agents.answer import AnswerWriter, make_answer_agent
from stage3.agents.calculation import calculate_facts, calculation_agent
from stage3.agents.comparison import compare_facts, comparison_agent
from stage3.agents.event_linker import event_linker_agent, link_events
from stage3.agents.fact_extraction import extract_facts, fact_extraction_agent

__all__ = [
    "AnswerWriter",
    "calculate_facts",
    "calculation_agent",
    "compare_facts",
    "comparison_agent",
    "event_linker_agent",
    "extract_facts",
    "fact_extraction_agent",
    "link_events",
    "make_answer_agent",
]
