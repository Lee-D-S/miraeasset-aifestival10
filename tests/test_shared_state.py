from __future__ import annotations

import copy
import unittest
from typing import get_type_hints

from langchain_core.messages import HumanMessage

from reasoner import build_reasoner_node
from shared_state import (
    AgentState,
    AgentStateUpdate,
    IMMUTABLE_STATE_FIELDS,
    STAGE_WRITE_FIELDS,
    make_initial_agent_state,
    validate_node_update,
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
                "retriever_result",
                "reasoner_result",
                "validator_result",
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
        self.assertEqual(STAGE_WRITE_FIELDS["interpreter"], {"intent", "route", "search_query"})
        self.assertEqual(
            STAGE_WRITE_FIELDS["retriever"],
            {"retriever_result", "retry_num", "search_attempts", "documents", "search_query", "search_queries"},
        )
        self.assertEqual(
            STAGE_WRITE_FIELDS["reasoner"],
            {"reasoner_result", "answer", "context", "messages", "gen_retry_num", "facts"},
        )
        self.assertEqual(
            STAGE_WRITE_FIELDS["validator"],
            {"validator_result", "answer", "messages", "validation_attempts"},
        )
        self.assertEqual(
            STAGE_WRITE_FIELDS["planner"],
            {"analysis_plan", "plan_status", "plan_failure_reason", "plan_trace", "planner_retry_num", "planner_attempts"},
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
        self.assertIsNone(first["retriever_result"])
        self.assertIsNone(first["reasoner_result"])
        self.assertIsNone(first["validator_result"])
        self.assertEqual(len(first["messages"]), 1)
        self.assertEqual(second["messages"], [])

    def test_initial_state_rejects_missing_request_id(self) -> None:
        with self.assertRaises(ValueError):
            make_initial_agent_state(question_id="", question="질문")

    def test_stage_updates_cannot_change_request_identity_or_original_question(self) -> None:
        state = make_initial_agent_state(question_id="Q-IMMUTABLE", question="question")
        with self.assertRaises(ValueError):
            validate_node_update("interpreter", state, {"question": "changed"})
        with self.assertRaises(ValueError):
            validate_node_update("interpreter", state, {"question_id": "Q-OTHER"})
        with self.assertRaises(ValueError):
            validate_node_update("interpreter", state, {"original_question": "changed"})

    def test_stage_ownership_rejects_cross_stage_fields(self) -> None:
        state = make_initial_agent_state(question_id="Q-OWNER", question="question")
        with self.assertRaises(ValueError):
            validate_node_update("interpreter", state, {"retriever_result": {}})
        with self.assertRaises(ValueError):
            validate_node_update("validator", state, {"facts": []})

    def test_control_counters_are_independent(self) -> None:
        state = make_initial_agent_state(question_id="Q-COUNTERS", question="question")
        self.assertEqual(
            {state[key] for key in ("search_attempts", "planner_attempts", "regeneration_attempts", "validation_attempts")},
            {0},
        )
        state.update(search_attempts=1, planner_attempts=2, regeneration_attempts=3, validation_attempts=4)
        self.assertEqual(state["search_attempts"], 1)
        self.assertEqual(state["planner_attempts"], 2)
        self.assertEqual(state["regeneration_attempts"], 3)
        self.assertEqual(state["validation_attempts"], 4)

    def test_reasoner_node_accepts_full_state_and_returns_a_partial_update(self) -> None:
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

        update = build_reasoner_node()(state)

        self.assertEqual(state, original)
        self.assertEqual(set(update), {"reasoner_result"})
        self.assertEqual(update["reasoner_result"]["status"], "need_clarify")
        self.assertNotIn("question_id", update)
        self.assertNotIn("question", update)


if __name__ == "__main__":
    unittest.main()
