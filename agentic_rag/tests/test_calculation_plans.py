import unittest

from agentic_rag.agents.calculation import make_calculation_agent
from agentic_rag.agents.calculation_planner import deterministic_plan, make_calculation_planner
from agentic_rag.deterministic.calculation_registry import execute_operation
from agentic_rag.deterministic.calculation_schema import validate_calculation_plan


class CalculationPlanTests(unittest.TestCase):
    def test_deterministic_plans_cover_margin_and_cagr(self):
        margin = deterministic_plan("2024년 영업이익률은?")
        cagr = deterministic_plan("2021년부터 2023년까지 매출 연평균 성장률은?")
        self.assertEqual(margin["operation"], "margin")
        self.assertEqual(cagr["periods"], ["2021", "2023"])

    def test_safe_registry_operations_and_zero_division(self):
        self.assertEqual(execute_operation("margin", [20, 100]), 20.0)
        self.assertAlmostEqual(execute_operation("cagr", [100, 121], periods=2), 10.0)
        with self.assertRaises(ValueError):
            execute_operation("divide", [1, 0])
        with self.assertRaises(ValueError):
            execute_operation("__import__", [1, 2])

    def test_invalid_plan_is_rejected(self):
        valid, reason = validate_calculation_plan({"operation": "eval", "metric": "revenue"})
        self.assertFalse(valid)
        self.assertIn("허용되지 않은", reason)

    def test_margin_executor_records_both_metric_evidence(self):
        agent = make_calculation_agent()
        result = agent({"normalized_question": "영업이익률", "cited_documents": [{"id": "d1", "source": "d.pdf", "text": "매출 100 영업이익 20"}]})
        self.assertEqual(result["calculations"]["result"], 20.0)
        self.assertEqual(result["agent_results"][0]["evidence_ids"], ["d1"])

    def test_units_are_normalized_before_percentage_change(self):
        agent = make_calculation_agent()
        result = agent({"normalized_question": "매출 증가율", "cited_documents": [{"id": "d1", "source": "a", "text": "매출 1억원"}, {"id": "d2", "source": "b", "text": "매출 2억원"}]})
        self.assertEqual(result["calculations"]["percentage_change"], 100.0)
        self.assertEqual(result["provenance"][0]["details"]["calculations"]["inputs"], [100000000.0, 200000000.0])

    def test_missing_mixed_units_are_rejected(self):
        agent = make_calculation_agent()
        result = agent({"normalized_question": "매출 증가율", "cited_documents": [{"id": "d1", "source": "a", "text": "매출 100"}, {"id": "d2", "source": "b", "text": "매출 2억원"}]})
        self.assertEqual(result["agent_results"][0]["status"], "insufficient")
        self.assertIn("단위", result["calculations"]["error"])

    def test_ambiguous_plan_uses_chat_client_only_when_needed(self):
        calls = []

        class FakeChat:
            def generate_json(self, messages, *, schema, profile):
                calls.append((messages, schema, profile))
                return {"operation": "percentage_change", "metric": "revenue", "targets": [], "periods": []}

        planner = make_calculation_planner(FakeChat())
        plan, source = planner("이 수치들을 비교해줘")
        self.assertEqual(source, "llm")
        self.assertEqual(plan["operation"], "percentage_change")
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
