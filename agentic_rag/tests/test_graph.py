import unittest

from agentic_rag.service import AgenticAnswerService


class FakeRetriever:
    def corp_names(self):
        return ["테스트기업"]

    def search(self, query, limit, filters):
        return [{"id": "1", "source": "doc.pdf", "text": "테스트 근거", "score": 0.9, "metadata": filters}]


class ComparisonRetriever:
    def corp_names(self):
        return ["기업A", "기업B"]

    def search(self, query, limit, filters):
        company = filters.get("corp_name", "unknown")
        return [{"id": company, "source": f"{company}.pdf", "text": f"{company} 매출 100", "score": 0.9, "metadata": {"corp_name": company}}]


class GraphTests(unittest.TestCase):
    def test_service_preserves_evidence_and_response_contract(self):
        service = AgenticAnswerService(retriever=FakeRetriever(), reranker=lambda q, docs: docs, generator=lambda q, docs, intent: "근거 기반 답변")
        response = service.answer("Q-1", "테스트기업의 사업 내용은?")
        self.assertEqual(response.answer, "근거 기반 답변")
        self.assertIn("doc.pdf", response.retrieved_context)

    def test_unsupported_question_falls_back_without_retrieval(self):
        service = AgenticAnswerService(retriever=FakeRetriever(), reranker=lambda q, docs: docs, generator=lambda q, docs, intent: "생성되면 안 됨")
        response = service.answer("Q-2", "주가 예측해줘")
        self.assertNotEqual(response.answer, "생성되면 안 됨")

    def test_comparison_uses_parallel_send_and_deterministic_merge(self):
        service = AgenticAnswerService(retriever=ComparisonRetriever(), reranker=lambda q, docs: docs, generator=lambda q, docs, intent: "비교 결과")
        response = service.answer("Q-3", "기업A와 기업B 비교")
        self.assertEqual(response.answer, "비교 결과")
        self.assertIn("기업A.pdf", response.retrieved_context)
        self.assertIn("기업B.pdf", response.retrieved_context)
