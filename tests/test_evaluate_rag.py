import unittest
from types import SimpleNamespace

from scripts.evaluate_rag import evaluate_case, run


class FakeService:
    def __init__(self, response):
        self.response = response

    def answer(self, question_id, question):
        return self.response


def response(answer, context):
    return SimpleNamespace(
        question_id="EVAL",
        question="question",
        answer=answer,
        retrieved_context=context,
        think_trace="trace",
    )


class EvaluationTests(unittest.TestCase):
    def test_evaluate_answer_checks_source_and_numeric_value(self):
        case = {
            "question_id": "EVAL-001",
            "question": "질문",
            "expected_source": ["quarter_2023_03"],
            "required_terms": ["삼성전자", "2023년 1분기"],
            "expected_values": ["63조 7,454억"],
            "expected_periods": ["2023년 1분기"],
            "answerable": True,
        }
        result = evaluate_case(
            case,
            FakeService(response(
                "삼성전자의 2023년 1분기 매출은 63조 7,454억 원입니다.",
                "[출처: /data/quarter_2023_03.xml]\n삼성전자 2023년 1분기 매출 63조 7,454억 원",
            )),
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["source_match"])
        self.assertTrue(result["numeric_match"])
        self.assertEqual(result["retrieved_context"], "출처: /data/quarter_2023_03.xml")

    def test_evaluate_unanswerable_case_requires_fallback(self):
        case = {
            "question_id": "EVAL-002",
            "question": "없는 질문",
            "expected_source": [],
            "answerable": False,
        }
        result = evaluate_case(
            case,
            FakeService(response("제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.", "")),
        )
        self.assertTrue(result["passed"])
        self.assertTrue(result["answerable_match"])

    def test_run_reports_failures_without_stopping(self):
        cases = [
            {"question_id": "PASS", "question": "q", "answerable": False},
            {"question_id": "FAIL", "question": "q", "answerable": True},
        ]

        class ServiceFactory:
            def __call__(self):
                return FakeService(response("제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.", ""))

        report = run(cases, ServiceFactory())
        self.assertEqual(report["summary"]["total"], 2)
        self.assertEqual(report["summary"]["passed"], 1)
        self.assertEqual(report["summary"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()
