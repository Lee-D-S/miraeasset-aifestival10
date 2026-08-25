from __future__ import annotations

import json
from pathlib import Path
import unittest

from stage3.service import Stage3Service
from stage3.contracts import Stage3Result, adapt_stage1_intent
from stage3.validation import validate_submission_response
from stage3.validation import validate_stage3_result


class Stage3ServiceTests(unittest.TestCase):
    def test_processes_lookup_and_returns_five_string_submission_fields(self):
        service = Stage3Service()
        intent = {
            "raw_question": "기업A의 2025년 연결 매출액은?",
            "normalized_question": "기업A의 2025년 연결 매출액은?",
            "route": "ok",
            "intent": "lookup",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2025], "base_months": [12]},
        }
        response = service.answer(
            question_id="Q-001",
            question=intent["raw_question"],
            stage1_intent=intent,
            stage2_result={"cited_documents": [{"id": "d1", "source": "a.xml", "text": "2025년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}}]},
        )
        valid, errors = validate_submission_response(response)
        self.assertTrue(valid, errors)
        self.assertEqual(set(response), {"question_id", "question", "retrieved_context", "think_trace", "answer"})
        self.assertEqual(response["question_id"], "Q-001")
        self.assertIn("100", response["answer"])
        self.assertIn("d1", response["retrieved_context"])

    def test_non_ok_stage1_route_does_not_process_documents(self):
        service = Stage3Service()
        result = service.process(question="매출액 얼마야?", stage1_intent={"question": "매출액 얼마야?", "route": "need_clarify", "intent": "lookup"}, stage2_result={"documents": [{"id": "d1", "text": "매출액 100억원"}]})
        self.assertEqual(result.status, "need_clarify")
        self.assertEqual(result.facts, [])
        self.assertEqual(result.citations, [])

    def test_comparison_answer_uses_value_rank(self):
        service = Stage3Service()
        intent = {"question": "기업A와 기업B 중 매출액이 큰 기업은?", "route": "ok", "intent": "compare", "metric": "revenue", "basis": "연결"}
        result = service.process(
            question=intent["question"],
            stage1_intent=intent,
            stage2_result={"cited_documents": [
                {"id": "a", "source": "a.xml", "score": 0.99, "text": "매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}},
                {"id": "b", "source": "b.xml", "score": 0.10, "text": "매출액 120억원", "metadata": {"corp_name": "기업B", "report_period": "2025-12", "basis": "연결"}},
            ]},
        )
        self.assertEqual(result.status, "success")
        self.assertEqual(result.comparison_results[0]["top"]["company"], "기업B")

    def test_pdf_path_only_result_requires_stage2_text(self):
        service = Stage3Service()
        result = service.process(
            question="기업A의 2025년 매출액은?",
            stage1_intent={"route": "ok", "intent": "lookup", "metric": "revenue", "basis": "연결", "time": {"years": [2025], "base_months": [12]}},
            stage2_result={"documents": [{"id": "pdf-only", "source": "report.pdf", "text": "", "metadata": {"file_format": "pdf", "file_path": "raw/report.pdf"}}]},
        )
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertIn("pdf-only: pdf_text_required", result.warnings)

    def test_unverified_hyperclova_answer_is_replaced_by_deterministic_fallback(self):
        class HallucinatingClient:
            def generate_text(self, _messages):
                return "매출액은 999억원입니다."

        service = Stage3Service(answer_client=HallucinatingClient())
        result = service.process(
            question="기업A의 2025년 매출액은?",
            stage1_intent={"route": "ok", "intent": "lookup", "metric": "revenue", "basis": "연결", "time": {"years": [2025], "base_months": [12]}},
            stage2_result={"documents": [{"id": "grounded", "text": "2025년 연결 매출액 100억원", "metadata": {"corp_name": "기업A", "report_period": "2025-12", "basis": "연결"}}]},
        )
        self.assertEqual(result.status, "insufficient_evidence")
        self.assertIn("100", result.answer)
        self.assertNotIn("999", result.answer)
        self.assertIn("validation_failed", result.trace)

    def test_validator_recomputes_calculation_and_rejects_tampered_result(self):
        intent = adapt_stage1_intent({"route": "ok", "intent": "calc", "metric": "revenue", "basis": "연결"})
        result = Stage3Result(
            status="success",
            answer="증감률은 99%입니다.",
            facts=[
                {"document_id": "old", "value": 100, "raw_value": "100", "normalized_value": 100, "period": "2024-12", "basis": "연결", "unit": "억원"},
                {"document_id": "new", "value": 120, "raw_value": "120", "normalized_value": 120, "period": "2025-12", "basis": "연결", "unit": "억원"},
            ],
            calculations=[{"status": "ok", "operation": "percentage_change", "inputs": [{"value": 100, "period": "2024-12"}, {"value": 120, "period": "2025-12"}], "result": 99, "evidence_ids": ["old", "new"]}],
            citations=[{"document_id": "old"}, {"document_id": "new"}],
        )
        valid, warnings = validate_stage3_result(result, intent)
        self.assertFalse(valid)
        self.assertTrue(any("재검증 실패" in warning for warning in warnings))

    def test_actual_stage1_fixture_to_structured_stage2_to_submission(self):
        fixture_path = Path(__file__).parent / "fixtures" / "stage1_intents.json"
        stage1_intent = json.loads(fixture_path.read_text(encoding="utf-8"))["lookup"]
        structured_path = Path(__file__).parent / "fixtures" / "samsung_dart.xml"
        result = Stage3Service().answer(
            question_id="Q-INTEGRATION",
            question=stage1_intent["raw_question"],
            stage1_intent=stage1_intent,
            stage2_result={
                "documents": [{
                    "id": "periodic_2025_samsung",
                    "source": "raw/periodic/삼성전자/2025.xml",
                    "text": structured_path.read_text(encoding="utf-8"),
                    "metadata": {
                        "corp_name": "삼성전자",
                        "doc_group": "periodic",
                        "doc_subtype": "annual",
                        "rcept_no": "20250000000000",
                        "rcept_dt": "20260301",
                        "base_year": 2025,
                        "base_month": 12,
                        "is_correction": False,
                        "file_format": "xml",
                    },
                }],
                "cited_documents": [],
                "retrieval_trace": ["fixture"]
            },
        )
        valid, errors = validate_submission_response(result)
        self.assertTrue(valid, errors)
        self.assertEqual(result["question_id"], "Q-INTEGRATION")
        self.assertIn("periodic_2025_samsung", result["retrieved_context"])
        self.assertIn("1000", result["answer"])


if __name__ == "__main__":
    unittest.main()
