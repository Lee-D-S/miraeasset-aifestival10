from __future__ import annotations

import unittest

from integration.supervisor import build_planner_tool
from integration import StageNodes, StagePipeline
from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.contracts import Stage3Fact
from stage3.deterministic.calculation_planner import (
    build_analysis_plan,
    build_state_analysis_plan,
    validate_analysis_plan,
)
from stage3.deterministic.calculations import execute_analysis_plan
from stage3 import build_stage3_node
from stage2.node import build_stage2_node
from stage2.retrieval import InMemoryRetriever, RetrievalConfig


def _intent():
    return adapt_stage1_intent(
        {
            "route": "ok",
            "intent": "compare",
            "question_type": "compare",
            "metric": "capex",
            "corps": [{"corp_name": "A"}, {"corp_name": "B"}],
            "time": {"years": [2025], "relative_terms": ["전년"]},
            "calculation": {"operation": "rank", "metric": "capex"},
            "query_plan": [{"metric": "revenue"}, {"metric": "capex"}],
        },
        question="A와 B 중 매출 대비 설비투자 비중이 전년 대비 가장 많이 증가한 기업은?",
    )


def _fact(company: str, metric: str, period: str, value: float) -> Stage3Fact:
    return Stage3Fact(
        metric=metric,
        label=metric,
        value=value,
        raw_value=value,
        unit="백만원",
        normalized_value=value,
        period=period,
        basis="연결",
        company=company,
        document_id=f"{company}-{metric}-{period}",
        source=f"{metric}-{period}.xml",
        evidence=f"{company} {metric} {period} {value}",
    )


class AnalysisPlanTests(unittest.TestCase):
    def test_complex_ratio_change_rank_is_compiled_once(self):
        plan = build_analysis_plan(_intent())

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual([item["metric"] for item in plan["requirements"]], ["capex", "revenue"])
        self.assertEqual(
            [step["operation"] for step in plan["steps"]],
            ["ratio_percent", "percentage_point_change", "rank"],
        )
        self.assertEqual(plan["output"]["ref"], "rank")
        self.assertEqual(plan["requirements"][0]["periods"], ["2024", "2025"])

    def test_plan_executor_preserves_fact_provenance_and_ranks_derived_change(self):
        plan = build_analysis_plan(_intent())
        assert plan is not None
        facts = [
            _fact("A", "capex", "2024", 10), _fact("A", "capex", "2025", 30),
            _fact("A", "revenue", "2024", 100), _fact("A", "revenue", "2025", 150),
            _fact("B", "capex", "2024", 20), _fact("B", "capex", "2025", 30),
            _fact("B", "revenue", "2024", 100), _fact("B", "revenue", "2025", 100),
        ]

        result = execute_analysis_plan(plan, facts)

        self.assertTrue(result["success"])
        self.assertEqual(result["comparisons"][0]["top"]["company"], "A")
        self.assertEqual(result["comparisons"][0]["top"]["value"], 10.0)
        self.assertTrue(all(item["document_id"] for item in result["derived_facts"]))
        self.assertIn("A-capex-2024", result["comparisons"][0]["evidence_ids"])
        self.assertIn("A-revenue-2025", result["comparisons"][0]["evidence_ids"])

    def test_invalid_plan_reference_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_analysis_plan({
                "schema_version": 1,
                "status": "ready",
                "capability": "calculation",
                "requirements": [{"id": "revenue", "metric": "revenue"}],
                "steps": [{"id": "calc", "operation": "divide", "inputs": ["revenue", "missing"]}],
                "output": {"ref": "calc"},
            })

    def test_default_graph_planner_is_not_a_noop(self):
        update = build_planner_tool()({
            "question": "삼성전자의 2024년과 2025년 매출액 증감률은?",
            "intent": {
                "route": "ok",
                "intent": "calc",
                "question_type": "calculation",
                "metric": "revenue",
                "time": {"years": [2024, 2025]},
                "calculation": {"operation": "percentage_change", "metric": "revenue"},
            },
        })

        self.assertEqual(update["plan_status"], "ready")
        self.assertEqual(update["analysis_plan"]["steps"][0]["operation"], "percentage_change")

    def test_unplannable_calculation_fails_closed_after_one_attempt(self):
        calls: list[str] = []

        def stage1(_state):
            calls.append("stage1")
            return {"route": "ok", "intent": {"route": "ok", "intent": "calc", "question_type": "calculation"}}

        def forbidden_stage2(_state):
            calls.append("stage2")
            raise AssertionError("계획을 만들지 못한 계산은 검색으로 진행하면 안 됩니다.")

        def stage4(_state):
            calls.append("stage4")
            return {"stage4_result": {"status": "success"}, "answer": "차단"}

        state = StagePipeline(
            StageNodes(stage1, forbidden_stage2, lambda _state: {}, stage4)
        ).invoke(question_id="Q-UNPLANNABLE", question="알 수 없는 계산")

        self.assertEqual(calls, ["stage1", "stage4"])
        self.assertEqual(state["planner_attempts"], 1)

    def test_llm_provider_is_only_used_when_deterministic_compiler_cannot_plan(self):
        class Provider:
            calls = 0

            def generate_json(self, _messages, *, schema, **_kwargs):
                self.calls += 1
                return {
                    "schema_version": schema["schema_version"],
                    "status": "ready",
                    "capability": "calculation",
                    "requirements": [{"id": "revenue", "metric": "revenue"}],
                    "steps": [{"id": "calculate", "operation": "sum", "inputs": ["revenue"]}],
                    "output": {"ref": "calculate"},
                }

        provider = Provider()
        update = build_state_analysis_plan(
            {
                "question": "삼성전자의 알 수 없는 수치 조합을 계산해줘",
                "intent": {"route": "ok", "intent": "calc", "question_type": "calculation"},
            },
            llm_client=provider,
            llm_enabled=True,
        )

        self.assertEqual(provider.calls, 1)
        self.assertEqual(update["plan_status"], "ready")
        self.assertEqual(update["plan_trace"][0], "planner=llm")

    def test_stage3_consumes_the_same_plan_after_requirement_retrieval(self):
        intent = _intent().to_dict()
        plan = build_analysis_plan(_intent())
        assert plan is not None
        state = {
            "question": "A와 B 중 매출 대비 설비투자 비중이 전년 대비 가장 많이 증가한 기업은?",
            "route": "ok",
            "intent": intent,
            "analysis_plan": plan,
            "stage2_result": {"cited_documents": [], "subresults": []},
        }
        documents = []
        for company, capex_values, revenue_values in (
            ("A", (10, 30), (100, 150)),
            ("B", (20, 30), (100, 100)),
        ):
            for period, capex, revenue in zip(("2024", "2025"), capex_values, revenue_values):
                metadata = {"corp_name": company, "report_period": f"{period}-12", "basis": "연결"}
                documents.extend([
                    {"id": f"{company}-capex-{period}", "source": "capex.xml", "text": f"{period} 연결 설비투자 {capex} 백만원", "metadata": metadata},
                    {"id": f"{company}-revenue-{period}", "source": "revenue.xml", "text": f"{period} 연결 매출액 {revenue} 백만원", "metadata": metadata},
                ])
        for requirement in plan["requirements"]:
            metric = requirement["metric"]
            result_documents = [
                document for document in documents
                if (metric == "capex" and "capex" in document["id"]) or (metric == "revenue" and "revenue" in document["id"])
            ]
            state["stage2_result"]["subresults"].append({
                "requirement_id": requirement["id"],
                "cited_documents": result_documents,
            })
            state["stage2_result"]["cited_documents"].extend(result_documents)

        result = build_stage3_node()(state)["stage3_result"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["comparison_results"][0]["top"]["company"], "A")
        self.assertTrue(any(fact["kind"] == "derived" for fact in result["facts"]))

    def test_stage2_uses_plan_requirements_instead_of_reinterpreting_query_plan(self):
        plan = build_analysis_plan(_intent())
        assert plan is not None
        documents = [
            {"id": "capex-a", "text": "A 설비투자 10 백만원", "metadata": {"corp_name": "A", "base_year": 2024}},
            {"id": "revenue-a", "text": "A 매출액 100 백만원", "metadata": {"corp_name": "A", "base_year": 2024}},
        ]
        state = {
            "question_id": "Q-PLAN",
            "question": "A와 B 중 매출 대비 설비투자 비중이 전년 대비 가장 많이 증가한 기업은?",
            "intent": _intent().to_dict(),
            "analysis_plan": plan,
            "route": "ok",
            "retry_num": 0,
            "search_attempts": 0,
        }

        result = build_stage2_node(
            retriever=InMemoryRetriever(documents),
            config=RetrievalConfig(final_limit=10),
        )(state)

        self.assertEqual(
            [item["requirement_id"] for item in result["stage2_result"]["subresults"]],
            ["capex", "revenue"],
        )
        self.assertEqual(set(result["search_queries"]), {"capex", "revenue"})


if __name__ == "__main__":
    unittest.main()
