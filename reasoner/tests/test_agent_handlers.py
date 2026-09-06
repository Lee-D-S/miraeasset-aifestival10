from __future__ import annotations

import unittest

from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.adapters.retriever import adapt_retriever_bundle
from reasoner.agents.calculation import calculation_agent
from reasoner.agents.comparison import comparison_agent
from reasoner.agents.event_linker import event_linker_agent
from reasoner.agents.fact_extraction import fact_extraction_agent


class AgentHandlerTests(unittest.TestCase):
    def test_fact_extraction_agent_returns_structured_result(self):
        intent = adapt_interpreter_intent({"route": "ok", "intent": "lookup", "metric": "revenue", "basis": "연결"})
        documents = adapt_retriever_bundle([
            {"id": "d1", "text": "매출액 100억원", "metadata": {"corp_name": "기업A"}}
        ]).documents
        result = fact_extraction_agent({"question": "매출액", "intent": intent, "documents": documents})
        self.assertEqual(result.agent, "fact_extractor")
        self.assertEqual(result.facts[0]["document_id"], "d1")

    def test_calculation_and_comparison_agents_use_fact_state(self):
        calc_intent = adapt_interpreter_intent({
            "route": "ok",
            "intent": "calc",
            "question_type": "calculation",
            "calculation": {"operation": "percentage_change"},
            "metric": "revenue",
            "basis": "연결",
            "normalized_question": "2024년에서 2025년 매출 증가율은?",
            "time": {"years": [2024, 2025], "base_months": [12]},
        })
        docs = adapt_retriever_bundle([
            {"id": "old", "text": "2024년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2024-12", "basis": "연결"}},
            {"id": "new", "text": "2025년 연결 매출액 120억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
        ]).documents
        facts = fact_extraction_agent({"question": calc_intent.question, "intent": calc_intent, "documents": docs}).facts
        calc_result = calculation_agent({"question": calc_intent.question, "intent": calc_intent, "facts": list(facts)})
        self.assertEqual(calc_result.status, "ok")
        self.assertEqual(calc_result.calculations[0]["result"], 20.0)

        compare_intent = adapt_interpreter_intent({
            "route": "ok",
            "intent": "compare",
            "metric": "revenue",
            "basis": "연결",
            "normalized_question": "기업A와 기업B 중 매출액이 큰 기업은?",
            "companies": ["기업A", "기업B"],
        })
        compare_docs = adapt_retriever_bundle([
            {"id": "a", "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
            {"id": "b", "text": "매출액 120억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
        ]).documents
        compare_facts = fact_extraction_agent({"question": compare_intent.question, "intent": compare_intent, "documents": compare_docs}).facts
        compare_result = comparison_agent({"question": compare_intent.question, "intent": compare_intent, "facts": list(compare_facts)})
        self.assertEqual(compare_result.status, "ok")
        self.assertEqual(compare_result.comparison_results[0]["top"]["company"], "기업B")

    def test_event_agent_keeps_source_ids(self):
        intent = adapt_interpreter_intent({"route": "ok", "intent": "exists", "question": "계약 해지 여부"})
        documents = adapt_retriever_bundle([
            {"id": "origin", "text": "단일판매 공급계약 체결 계약명 A", "metadata": {"corp_name": "기업A", "rcept_no": "origin-rcept", "report_nm": "계약체결"}},
            {"id": "termination", "text": "단일판매 공급계약 해지 계약명 A", "original_rcept_no": "origin-rcept", "metadata": {"corp_name": "기업A", "report_nm": "계약해지"}},
        ]).documents
        result = event_linker_agent({"question": intent.question, "intent": intent, "documents": documents})
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.evidence_ids, ("origin", "termination"))


if __name__ == "__main__":
    unittest.main()
