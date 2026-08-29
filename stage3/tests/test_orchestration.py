from __future__ import annotations

import unittest
from unittest.mock import patch

from stage3.orchestration.runtime import langgraph_available, resolve_execution_mode
from stage3.service import Stage3Service


def _lookup_input() -> tuple[str, dict, dict]:
    question = "기업A의 2025년 연결 매출액은?"
    intent = {
        "raw_question": question,
        "normalized_question": question,
        "route": "ok",
        "intent": "lookup",
        "question_type": "lookup",
        "metric": "revenue",
        "basis": "연결",
        "time": {"years": [2025], "base_months": [12]},
    }
    stage2 = {
        "documents": [{
            "id": "lookup-1",
            "source": "lookup.xml",
            "text": "2025년 연결 매출액 100억원",
            "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"},
        }],
    }
    return question, intent, stage2


class OrchestrationTests(unittest.TestCase):
    @unittest.skipUnless(langgraph_available(), "LangGraph가 설치된 환경에서 실행")
    def test_graph_contains_only_one_stage3_node(self):
        from stage3.orchestration.langgraph_graph import build_graph

        graph = build_graph(answer_writer=Stage3Service(execution_mode="stdlib").answer_writer)
        node_names = set(graph.nodes)
        self.assertEqual(node_names, {"__start__", "stage3"})

    @unittest.skipUnless(langgraph_available(), "LangGraph가 설치된 환경에서 실행")
    def test_langgraph_and_stdlib_share_single_node_result_contract(self):
        question, intent, stage2 = _lookup_input()
        graph_result = Stage3Service(execution_mode="langgraph").process(
            question=question, stage1_intent=intent, stage2_result=stage2
        )
        stdlib_result = Stage3Service(execution_mode="stdlib").process(
            question=question, stage1_intent=intent, stage2_result=stage2
        )
        for field in ("status", "answer", "facts", "calculations", "comparison_results", "linked_events", "citations"):
            self.assertEqual(getattr(graph_result, field), getattr(stdlib_result, field), field)

    @unittest.skipUnless(langgraph_available(), "LangGraph가 설치된 환경에서 실행")
    def test_langgraph_result_has_no_internal_stage4_validator(self):
        question, intent, stage2 = _lookup_input()
        with patch("stage3.validation.validate_stage3_result", side_effect=AssertionError("Stage4 validator must not run")):
            result = Stage3Service(execution_mode="langgraph").process(
                question=question, stage1_intent=intent, stage2_result=stage2
            )
        self.assertEqual(result.status, "success")
        self.assertTrue(result.answer)

    def test_langgraph_mode_missing_dependency_has_clear_error(self):
        with patch("stage3.orchestration.runtime.langgraph_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "langgraph 패키지"):
                resolve_execution_mode("langgraph")


if __name__ == "__main__":
    unittest.main()
