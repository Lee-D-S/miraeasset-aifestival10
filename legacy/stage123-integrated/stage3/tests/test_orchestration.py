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
    def test_graph_compiles_without_search_or_rerank_nodes(self):
        from stage3.orchestration.langgraph_graph import build_graph

        graph = build_graph(answer_writer=Stage3Service(execution_mode="stdlib").answer_writer)
        node_names = set(graph.nodes)
        self.assertIn("fact_extraction_agent", node_names)
        self.assertIn("calculation_agent", node_names)
        self.assertIn("comparison_agent", node_names)
        self.assertIn("event_linker_agent", node_names)
        self.assertIn("validation_agent", node_names)
        self.assertNotIn("retrieve", node_names)
        self.assertNotIn("rerank", node_names)
        self.assertNotIn("search", node_names)

    @unittest.skipUnless(langgraph_available(), "LangGraph가 설치된 환경에서 실행")
    def test_langgraph_and_stdlib_share_analysis_result_contract(self):
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
    def test_send_fanout_records_calculation_and_comparison_agents(self):
        question = "기업A와 기업B의 2025년 매출액 증감률을 비교하고 순위를 알려줘"
        intent = {
            "question": question,
            "normalized_question": question,
            "route": "ok",
            "intent": "compare",
            "metric": "revenue",
            "basis": "연결",
            "companies": ["기업A", "기업B"],
            "time": {"years": [2025], "base_months": [12]},
        }
        stage2 = {"documents": [
            {"id": "a", "source": "a.xml", "text": "2025년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "b", "source": "b.xml", "text": "2025년 연결 매출액 120억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
        ]}
        result = Stage3Service(execution_mode="langgraph").process(
            question=question, stage1_intent=intent, stage2_result=stage2
        )
        agents = {item["agent"] for item in result.agent_results}
        self.assertTrue({"supervisor", "fact_extractor", "calculation", "comparison", "answer", "validator"}.issubset(agents))

    def test_langgraph_mode_missing_dependency_has_clear_error(self):
        with patch("stage3.orchestration.runtime.langgraph_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "langgraph 패키지"):
                resolve_execution_mode("langgraph")


if __name__ == "__main__":
    unittest.main()
