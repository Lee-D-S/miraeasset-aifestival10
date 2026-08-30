from __future__ import annotations

import unittest

from integration import StageNodes, StagePipeline
from integration.supervisor import SupervisorDecision, build_supervisor_node


class SupervisorGraphTests(unittest.TestCase):
    def test_normal_path_is_supervised_between_stages(self) -> None:
        calls: list[str] = []

        def stage1(_state):
            calls.append("stage1")
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def stage2(_state):
            calls.append("stage2")
            return {"stage2_result": {"status": "ok", "cited_documents": [{"id": "d1"}]}}

        def stage3(_state):
            calls.append("stage3")
            return {"stage3_result": {"status": "success", "answer": "답"}, "answer": "답"}

        def stage4(_state):
            calls.append("stage4")
            return {"stage4_result": {"status": "success"}, "answer": "답"}

        state = StagePipeline(StageNodes(stage1, stage2, stage3, stage4)).invoke(
            question_id="Q-001", question="질문"
        )

        self.assertEqual(calls, ["stage1", "stage2", "stage3", "stage4"])
        self.assertEqual(state["supervisor_action"], "finish")
        self.assertGreaterEqual(state["supervisor_steps"], 4)

    def test_empty_search_is_retried_once_then_unanswerable(self) -> None:
        calls = 0

        def stage1(_state):
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def stage2(_state):
            nonlocal calls
            calls += 1
            return {"stage2_result": {"status": "not_found", "cited_documents": []}}

        def stage3(_state):
            raise AssertionError("문서가 없으면 Stage3로 가지 않아야 합니다.")

        def stage4(state):
            return {"stage4_result": {"status": "success"}, "answer": state["route"]}

        state = StagePipeline(StageNodes(stage1, stage2, stage3, stage4)).invoke(
            question_id="Q-002", question="없는 질문"
        )

        self.assertEqual(calls, 2)
        self.assertEqual(state["route"], "unanswerable")
        self.assertEqual(state["retry_num"], 1)

    def test_invalid_llm_action_fails_closed(self) -> None:
        def stage1(_state):
            return {"route": "ok", "intent": {"intent": "lookup", "route": "ok"}}

        def supervisor(*, phase, state):
            return SupervisorDecision("run_stage2" if phase == "after_stage1" else "not_allowed")  # type: ignore[arg-type]

        def stage4(state):
            return {"stage4_result": {"status": "success"}, "answer": state["route"]}

        from integration.supervisor import build_supervisor_node

        state = StagePipeline(
            StageNodes(stage1, lambda _state: {"stage2_result": {"status": "ok", "cited_documents": [{"id": "d"}]}}, lambda _state: {"stage3_result": {"status": "success"}}, stage4, supervisor=build_supervisor_node(supervisor))
        ).invoke(question_id="Q-003", question="질문")

        self.assertEqual(state["route"], "ok")
        self.assertEqual(state["termination_reason"], "validation_failed")

    def test_supervisor_phase_and_step_limits_fail_closed(self) -> None:
        node = build_supervisor_node(max_supervisor_steps=1)
        first = node({"supervisor_phase": "after_stage1", "route": "ok", "supervisor_steps": 0})
        self.assertEqual(first["supervisor_action"], "run_stage2")
        limited = node({"supervisor_phase": "after_stage1", "route": "ok", "supervisor_steps": 1})
        self.assertEqual(limited["supervisor_action"], "fail_closed")
        invalid = node({"supervisor_phase": "invalid", "route": "ok", "supervisor_steps": 0})
        self.assertEqual(invalid["supervisor_action"], "fail_closed")


if __name__ == "__main__":
    unittest.main()
