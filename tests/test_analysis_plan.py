from __future__ import annotations

import unittest

from integration.supervisor import build_planner_tool
from integration import StageNodes, StagePipeline
from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.contracts import ReasonerFact
from reasoner.deterministic.calculation_planner import (
    build_analysis_plan,
    build_state_analysis_plan,
    validate_analysis_plan,
)
from reasoner.deterministic.calculations import execute_analysis_plan
from reasoner import build_reasoner_node
from retriever.node import build_retriever_node
from retriever.retrieval import InMemoryRetriever, RetrievalConfig


def _intent():
    return adapt_interpreter_intent(
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


def _fact(company: str, metric: str, period: str, value: float) -> ReasonerFact:
    return ReasonerFact(
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

        result = execute_analysis_plan(plan, facts, intent=_intent())

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

        def interpreter(_state):
            calls.append("interpreter")
            return {"route": "ok", "intent": {"route": "ok", "intent": "calc", "question_type": "calculation"}}

        def forbidden_retriever(_state):
            calls.append("retriever")
            raise AssertionError("계획을 만들지 못한 계산은 검색으로 진행하면 안 됩니다.")

        def validator(_state):
            calls.append("validator")
            return {"validator_result": {"status": "success"}, "answer": "차단"}

        state = StagePipeline(
            StageNodes(interpreter, forbidden_retriever, lambda _state: {}, validator)
        ).invoke(question_id="Q-UNPLANNABLE", question="알 수 없는 계산")

        self.assertEqual(calls, ["interpreter", "validator"])
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

    def test_llm_planner_prompt_lists_registered_metrics_and_operations(self):
        # LLM이 분자·분모 metric을 스스로 고르게 하려면, validate_analysis_plan이
        # 실제로 허용하는 metric·operation 집합을 프롬프트에 그대로 알려줘야 한다.
        # 없으면 LLM이 이름을 추측하다 등록되지 않은 metric을 내놓고
        # validate_analysis_plan에서 거부당해 llm_plan_invalid로 끝난다.
        captured = {}

        class Provider:
            def generate_json(self, messages, *, schema, **_kwargs):
                captured["messages"] = messages
                return {
                    "schema_version": schema["schema_version"],
                    "status": "ready",
                    "capability": "calculation",
                    "requirements": [
                        {"id": "rnd", "metric": "rnd"},
                        {"id": "operating_profit", "metric": "operating_profit"},
                    ],
                    "steps": [{"id": "calculate", "operation": "ratio_percent", "inputs": ["rnd", "operating_profit"]}],
                    "output": {"ref": "calculate"},
                }

        update = build_state_analysis_plan(
            {
                "question": "삼성전자의 알 수 없는 수치 조합을 계산해줘",
                "intent": {"route": "ok", "intent": "calc", "question_type": "calculation"},
            },
            llm_client=Provider(),
            llm_enabled=True,
        )

        system_message = captured["messages"][0]["content"]
        self.assertIn("rnd(연구개발비", system_message)
        self.assertIn("operating_profit(영업이익", system_message)
        self.assertIn("ratio_percent", system_message)
        self.assertEqual(update["plan_status"], "ready")

    def test_reasoner_consumes_the_same_plan_after_requirement_retrieval(self):
        intent = _intent().to_dict()
        plan = build_analysis_plan(_intent())
        assert plan is not None
        state = {
            "question": "A와 B 중 매출 대비 설비투자 비중이 전년 대비 가장 많이 증가한 기업은?",
            "route": "ok",
            "intent": intent,
            "analysis_plan": plan,
            "retriever_result": {"cited_documents": [], "subresults": []},
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
            state["retriever_result"]["subresults"].append({
                "requirement_id": requirement["id"],
                "cited_documents": result_documents,
            })
            state["retriever_result"]["cited_documents"].extend(result_documents)

        result = build_reasoner_node()(state)["reasoner_result"]

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["comparison_results"][0]["top"]["company"], "A")
        self.assertTrue(any(fact["kind"] == "derived" for fact in result["facts"]))

    def test_ratio_plan_resolves_text_only_metric_via_question_position(self):
        # T4 규칙: "rnd"는 numeric_labels가 없던 시절엔 분자·분모가 모두
        # "operating_profit"으로 무너져 requirement id가 중복되고
        # validate_analysis_plan이 ValueError를 던졌다 (deterministic_plan_unavailable).
        # 지금은 metric_registry의 numeric_labels로 "연구개발비"를 찾아 정상 계획을 만든다.
        intent = adapt_interpreter_intent(
            {
                "route": "ok",
                "intent": "calc",
                "question_type": "calculation",
                "metric": "rnd",
                "corps": [{"corp_name": "삼성전자"}],
                "time": {"years": [2024]},
                "calculation": {"operation": "ratio_percent", "metric": "rnd", "denominator_metric": "operating_profit"},
            },
            question="삼성전자의 2024년 영업이익 대비 연구개발비 비중은?",
        )

        plan = build_analysis_plan(intent)

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual([item["metric"] for item in plan["requirements"]], ["rnd", "operating_profit"])
        self.assertEqual(plan["steps"][0]["operation"], "ratio_percent")

    def test_ratio_metrics_fallback_never_collides_into_duplicate_requirement(self):
        from reasoner.deterministic.calculation_planner import _metric_candidates, _ratio_metrics

        # T4가 고치기 전에 실제로 밟았던 경로: 요청한 numerator metric이
        # metric_registry에 numeric_labels가 없으면(등록되지 않은 metric도 같은 처지),
        # 위치 기반 탐지가 실패하고 fallback이 candidates[0]으로 넘어간다. 질문에
        # "영업이익"만 등장하므로 candidates는 ["operating_profit"] 하나뿐이고, 이게
        # 그대로 numerator가 되어 denominator_metric("operating_profit")과 충돌했다.
        # 예전 fallback은 이 상태를 그대로 반환해서 build_analysis_plan이 numerator·
        # denominator가 같은 requirement 두 개짜리 계획을 만들었고, validate_analysis_plan이
        # id 중복으로 ValueError를 던졌다(deterministic_plan_unavailable:ValueError). 지금은
        # denominator를 "revenue"로 돌려서 같은 metric 두 개짜리 requirement가 나오지 않는다.
        intent = adapt_interpreter_intent(
            {
                "route": "ok",
                "intent": "calc",
                "question_type": "calculation",
                "metric": "등록되지않은metric",
                "corps": [{"corp_name": "삼성전자"}],
                "time": {"years": [2024]},
                "calculation": {"operation": "ratio_percent", "metric": "등록되지않은metric", "denominator_metric": "operating_profit"},
            },
            question="삼성전자의 2024년 영업이익 대비 등록되지않은metric 비중은?",
        )
        candidates = _metric_candidates(intent, intent.question)
        self.assertEqual(candidates, ["operating_profit"])

        numerator, denominator = _ratio_metrics(intent, intent.question, candidates)

        self.assertEqual(numerator, "operating_profit")
        self.assertNotEqual(numerator, denominator)

        plan = build_analysis_plan(intent)
        assert plan is not None
        self.assertEqual(len({item["id"] for item in plan["requirements"]}), len(plan["requirements"]))

    def test_retriever_uses_plan_requirements_instead_of_reinterpreting_query_plan(self):
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

        result = build_retriever_node(
            retriever=InMemoryRetriever(documents),
            config=RetrievalConfig(final_limit=10),
        )(state)

        self.assertEqual(
            [item["requirement_id"] for item in result["retriever_result"]["subresults"]],
            ["capex", "revenue"],
        )
        self.assertEqual(set(result["search_queries"]), {"capex", "revenue"})


if __name__ == "__main__":
    unittest.main()
