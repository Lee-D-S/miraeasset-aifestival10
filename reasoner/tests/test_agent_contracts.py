from __future__ import annotations

import unittest

from reasoner.contracts import AgentResult, HandoffRequest, Provenance
from reasoner.handoff import create_handoff
from reasoner.registry import AgentRegistry, AgentSpec
from reasoner.agents.schemas import validate_agent_result


def _result_handler(_state):
    return AgentResult(agent="worker", status="ok")


class AgentContractTests(unittest.TestCase):
    def test_agent_result_and_provenance_are_serializable(self):
        result = AgentResult(
            agent="fact_extractor",
            status="ok",
            facts=({"document_id": "doc-1"},),
            evidence_ids=("doc-1",),
            confidence=0.9,
            trace=("facts=1",),
        )
        provenance = Provenance("fact_extractor", "질문", ("doc-1",), ("a.xml",), 0.9)

        self.assertEqual(result.to_dict()["evidence_ids"], ["doc-1"])
        self.assertEqual(provenance.to_dict()["document_ids"], ["doc-1"])
        self.assertTrue(validate_agent_result(result.to_dict())[0])

    def test_registry_validates_unique_names_and_handoffs(self):
        registry = AgentRegistry([
            AgentSpec("supervisor", "route", _result_handler, ("worker",)),
            AgentSpec("worker", "work", _result_handler),
        ])
        handoff = create_handoff(
            registry,
            "supervisor",
            HandoffRequest("supervisor", "worker", "작업", "intent=lookup"),
        )
        self.assertEqual(handoff["target"], "worker")

        with self.assertRaises(ValueError):
            create_handoff(
                registry,
                "worker",
                HandoffRequest("worker", "supervisor", "작업", "허용되지 않음"),
            )

    def test_registry_rejects_unknown_handoff_target(self):
        with self.assertRaises(ValueError):
            AgentRegistry([AgentSpec("supervisor", "route", _result_handler, ("missing",))])


if __name__ == "__main__":
    unittest.main()
