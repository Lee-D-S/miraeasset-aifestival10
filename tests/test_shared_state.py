from __future__ import annotations

import copy
import unittest
from typing import get_type_hints

from langchain_core.messages import HumanMessage

from stage3 import build_stage3_node
from shared_state import (
    AgentState,
    AgentStateUpdate,
    IMMUTABLE_STATE_FIELDS,
    STAGE_WRITE_FIELDS,
    make_initial_agent_state,
)


class SharedStateTests(unittest.TestCase):
    def test_agent_state_contains_the_full_cross_stage_contract(self) -> None:
        annotations = get_type_hints(AgentState)
        self.assertTrue(
            {
                "messages",
                "question_id",
                "question",
                "intent",
                "route",
                "stage2_result",
                "stage3_result",
                "stage4_result",
                "answer",
                "context",
                "documents",
                "facts",
                "retry_num",
                "gen_retry_num",
            }.issubset(annotations)
        )

    def test_partial_update_excludes_immutable_request_fields(self) -> None:
        self.assertEqual(AgentStateUpdate.__required_keys__, frozenset())
        self.assertEqual(IMMUTABLE_STATE_FIELDS, {"question_id", "question"})
        self.assertNotIn("question_id", get_type_hints(AgentStateUpdate))
        self.assertNotIn("question", get_type_hints(AgentStateUpdate))

    def test_stage_write_ownership_matches_the_shared_contract(self) -> None:
        self.assertEqual(STAGE_WRITE_FIELDS["stage1"], {"intent", "route", "search_query"})
        self.assertEqual(
            STAGE_WRITE_FIELDS["stage2"],
            {"stage2_result", "retry_num", "search_attempts", "documents", "search_query"},
        )
        self.assertEqual(
            STAGE_WRITE_FIELDS["stage3"],
            {"stage3_result", "answer", "context", "messages", "gen_retry_num", "facts"},
        )
        self.assertEqual(
            STAGE_WRITE_FIELDS["stage4"],
            {"stage4_result", "answer", "messages", "validation_attempts"},
        )

    def test_initial_state_has_all_defaults_and_fresh_messages(self) -> None:
        first = make_initial_agent_state(
            question_id="Q-001",
            question="질문",
            messages=[HumanMessage(content="질문")],
        )
        second = make_initial_agent_state(question_id="Q-002", question="다른 질문")

        self.assertEqual(first["question_id"], "Q-001")
        self.assertEqual(first["question"], "질문")
        self.assertEqual(first["retry_num"], 0)
        self.assertEqual(first["gen_retry_num"], 0)
        self.assertIsNone(first["stage2_result"])
        self.assertIsNone(first["stage3_result"])
        self.assertIsNone(first["stage4_result"])
        self.assertEqual(len(first["messages"]), 1)
        self.assertEqual(second["messages"], [])

    def test_initial_state_rejects_missing_request_id(self) -> None:
        with self.assertRaises(ValueError):
            make_initial_agent_state(question_id="", question="질문")

    def test_stage3_node_accepts_full_state_and_returns_a_partial_update(self) -> None:
        state = make_initial_agent_state(question_id="Q-003", question="질문")
        state["intent"] = {
            "raw_question": state["question"],
            "normalized_question": state["question"],
            "intent": "unknown",
            "route": "need_clarify",
            "warnings": [],
        }
        state["route"] = "need_clarify"
        original = copy.deepcopy(state)

        update = build_stage3_node()(state)

        self.assertEqual(state, original)
        self.assertEqual(set(update), {"stage3_result"})
        self.assertEqual(update["stage3_result"]["status"], "need_clarify")
        self.assertNotIn("question_id", update)
        self.assertNotIn("question", update)


if __name__ == "__main__":
    unittest.main()
