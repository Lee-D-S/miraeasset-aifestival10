import unittest

from agentic_rag.contracts import HandoffRequest
from agentic_rag.handoff import create_handoff
from agentic_rag.registry import AgentRegistry, AgentSpec


class RegistryTests(unittest.TestCase):
    def test_duplicate_names_rejected(self):
        with self.assertRaises(ValueError):
            AgentRegistry([AgentSpec("x", "one", lambda s: {}), AgentSpec("x", "two", lambda s: {})])

    def test_unknown_destination_rejected(self):
        with self.assertRaises(ValueError):
            AgentRegistry([AgentSpec("x", "one", lambda s: {}, ("missing",))])

    def test_handoff_is_typed_and_validated(self):
        registry = AgentRegistry([AgentSpec("supervisor", "route", lambda s: {}, ("worker",)), AgentSpec("worker", "work", lambda s: {})])
        result = create_handoff(registry, "supervisor", HandoffRequest("worker", "search", "semantic match"))
        self.assertEqual(result["target"], "worker")
        with self.assertRaises(ValueError):
            create_handoff(registry, "worker", HandoffRequest("supervisor", "x", "y"))

