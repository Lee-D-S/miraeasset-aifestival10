import unittest

from agentic_rag.agents.calculation import calculation_agent
from agentic_rag.agents.comparison import comparison_agent
from agentic_rag.agents.event_linker import event_linker_agent
from agentic_rag.agents.fact_extractor import fact_extractor_agent


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "normalized_question": "기업A와 기업B 매출 증가율과 합병 사건은?",
            "cited_documents": [
                {"id": "a", "source": "a.pdf", "text": "기업A 매출 100 합병 계약", "metadata": {"corp_name": "기업A"}},
                {"id": "b", "source": "b.pdf", "text": "기업B 매출 120", "metadata": {"corp_name": "기업B"}},
            ],
        }

    def test_each_specialist_returns_agent_result_and_provenance(self):
        for handler in (comparison_agent, calculation_agent, event_linker_agent, fact_extractor_agent):
            result = handler(self.state)
            self.assertEqual(len(result["agent_results"]), 1)
            self.assertEqual(len(result["provenance"]), 1)
            self.assertEqual(result["agent_results"][0]["agent"], result["provenance"][0]["agent"])

    def test_calculation_is_deterministic(self):
        result = calculation_agent(self.state)
        self.assertEqual(result["calculations"]["percentage_change"], 20.0)

