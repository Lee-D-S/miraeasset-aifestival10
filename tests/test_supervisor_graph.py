from __future__ import annotations

import unittest

from integration import StageNodes, StagePipeline
from integration.rate_limit import RateLimitBlocked
from integration.supervisor import SupervisorDecision, build_supervisor_node
from validator.node import build_answer_regeneration_node


class SupervisorGraphTests(unittest.TestCase):
    def test_normal_path_is_supervised_between_stages(self) -> None:
        calls: list[str] = []

        def interpreter(_state):
            calls.append("interpreter")
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def retriever(_state):
            calls.append("retriever")
            return {"retriever_result": {"status": "ok", "cited_documents": [{"id": "d1"}]}}

        def reasoner(_state):
            calls.append("reasoner")
            return {"reasoner_result": {"status": "success", "answer": "답"}, "answer": "답"}

        def validator(_state):
            calls.append("validator")
            return {"validator_result": {"status": "success"}, "answer": "답"}

        state = StagePipeline(StageNodes(interpreter, retriever, reasoner, validator)).invoke(
            question_id="Q-001", question="질문"
        )

        self.assertEqual(calls, ["interpreter", "retriever", "reasoner", "validator"])
        self.assertEqual(state["supervisor_action"], "finish")
        self.assertGreaterEqual(state["supervisor_steps"], 4)

    def test_empty_search_is_retried_once_then_unanswerable(self) -> None:
        calls = 0

        def interpreter(_state):
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def retriever(_state):
            nonlocal calls
            calls += 1
            return {"retriever_result": {"status": "not_found", "cited_documents": []}}

        def reasoner(_state):
            raise AssertionError("문서가 없으면 Reasoner로 가지 않아야 합니다.")

        def validator(state):
            return {"validator_result": {"status": "success"}, "answer": state["route"]}

        state = StagePipeline(StageNodes(interpreter, retriever, reasoner, validator)).invoke(
            question_id="Q-002", question="없는 질문"
        )

        self.assertEqual(calls, 2)
        self.assertEqual(state["route"], "unanswerable")
        self.assertEqual(state["retry_num"], 1)

    def test_invalid_llm_action_fails_closed(self) -> None:
        def interpreter(_state):
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def supervisor(*, phase, state):
            return SupervisorDecision("run_retriever" if phase == "after_interpreter" else "not_allowed")  # type: ignore[arg-type]

        def validator(state):
            return {"validator_result": {"status": "success"}, "answer": state["route"]}

        from integration.supervisor import build_supervisor_node

        state = StagePipeline(
            StageNodes(interpreter, lambda _state: {"retriever_result": {"status": "ok", "cited_documents": [{"id": "d"}]}}, lambda _state: {"reasoner_result": {"status": "success"}}, validator, supervisor=build_supervisor_node(supervisor))
        ).invoke(question_id="Q-003", question="질문")

        self.assertEqual(state["route"], "ok")
        self.assertEqual(state["termination_reason"], "validation_failed")

    def test_supervisor_phase_and_step_limits_fail_closed(self) -> None:
        node = build_supervisor_node(max_supervisor_steps=1)
        first = node({"supervisor_phase": "after_interpreter", "route": "ok", "supervisor_steps": 0})
        self.assertEqual(first["supervisor_action"], "run_retriever")
        limited = node({"supervisor_phase": "after_interpreter", "route": "ok", "supervisor_steps": 1})
        self.assertEqual(limited["supervisor_action"], "fail_closed")
        invalid = node({"supervisor_phase": "invalid", "route": "ok", "supervisor_steps": 0})
        self.assertEqual(invalid["supervisor_action"], "fail_closed")

    def test_supervisor_without_regeneration_capability_fails_closed(self) -> None:
        node = build_supervisor_node(allow_regeneration=False)
        update = node({
            "supervisor_phase": "after_validator",
            "validator_result": {"status": "validation_failed"},
            "regeneration_attempts": 0,
            "supervisor_steps": 0,
        })
        self.assertEqual(update["supervisor_action"], "fail_closed")

    def test_injected_regeneration_runs_once_after_validator_failure(self) -> None:
        calls: list[str] = []

        def interpreter(_state):
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def retriever(_state):
            return {"retriever_result": {"status": "ok", "cited_documents": [{"id": "d1"}]}}

        def reasoner(_state):
            return {"reasoner_result": {"status": "success"}, "answer": "첫 답"}

        def validator(state):
            calls.append(str(state.get("answer")))
            if len(calls) == 1:
                return {"validator_result": {"status": "validation_failed"}, "answer": "실패 답"}
            return {"validator_result": {"status": "success"}, "answer": "재생성 답"}

        def regeneration(state):
            return {
                "answer": "재생성 답",
                "regeneration_attempts": int(state.get("regeneration_attempts", 0) or 0) + 1,
            }

        state = StagePipeline(
            StageNodes(interpreter, retriever, reasoner, validator, answer_regeneration=regeneration)
        ).invoke(question_id="Q-005", question="질문")

        self.assertEqual(calls, ["첫 답", "재생성 답"])
        self.assertEqual(state["answer"], "재생성 답")
        self.assertEqual(state["regeneration_attempts"], 1)

    def test_rate_limited_regeneration_keeps_first_answer(self) -> None:
        class RateLimitedClient:
            def generate_text(self, _messages):
                raise RateLimitBlocked("provider token budget exhausted", 10)

        node = build_answer_regeneration_node(answer_client=RateLimitedClient())
        result = node({"answer": "첫 답", "regeneration_attempts": 0})
        self.assertEqual(result["answer"], "첫 답")
        self.assertEqual(result["regeneration_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
