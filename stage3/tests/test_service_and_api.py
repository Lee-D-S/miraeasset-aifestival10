from __future__ import annotations

import unittest

from stage3.service import Stage3Service
from stage3.validation import validate_submission_response


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


if __name__ == "__main__":
    unittest.main()
