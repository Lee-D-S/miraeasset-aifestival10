import unittest

from agentic_rag.deterministic.alternatives import AlternativeFinder
from agentic_rag.service import AgenticAnswerService


class AlternativeRetriever:
    def corp_names(self):
        return ["기업A", "기업B", "기업C"]

    def search(self, _query, limit, filters):
        if filters.get("corp_name") == "기업A":
            return [
                {"id": "a-2024", "source": "a-2024.pdf", "score": 0.8, "metadata": {"corp_name": "기업A", "report_period": "2024-12"}},
                {"id": "a-2023", "source": "a-2023.pdf", "score": 0.9, "metadata": {"corp_name": "기업A", "report_period": "2023-12"}},
                {"id": "a-2023", "source": "duplicate.pdf", "score": 0.7, "metadata": {"corp_name": "기업A", "report_period": "2023-12"}},
            ][:limit]
        if filters.get("report_period") == "2024-12":
            return [
                {"id": "b-2024", "source": "b-2024.pdf", "score": 0.7, "metadata": {"corp_name": "기업B", "report_period": "2024-12"}},
                {"id": "c-2024", "source": "c-2024.pdf", "score": 0.6, "metadata": {"corp_name": "기업C", "report_period": "2024-12"}},
                {"id": "a-2024", "source": "a-2024.pdf", "score": 1.0, "metadata": {"corp_name": "기업A", "report_period": "2024-12"}},
            ][:limit]
        return []


class AlternativeFinderTests(unittest.TestCase):
    def test_finds_separate_company_and_period_alternatives(self):
        result = AlternativeFinder(AlternativeRetriever(), limit=2).find(
            "기업A의 2024년 사업보고서 매출은?"
        )
        self.assertEqual([doc["id"] for doc in result["same_company"]], ["a-2023"])
        self.assertEqual([doc["id"] for doc in result["same_period"]], ["b-2024", "c-2024"])

    def test_finder_failure_is_safe(self):
        class Broken:
            def corp_names(self):
                return ["기업A"]

            def search(self, *_args, **_kwargs):
                raise TimeoutError("offline")

        self.assertEqual(AlternativeFinder(Broken()).find("기업A의 2024년 자료"), {"same_company": [], "same_period": []})

    def test_service_includes_reason_and_reference_documents_on_fallback(self):
        class EmptyPrimaryRetriever(AlternativeRetriever):
            def search(self, _query, limit, filters):
                if not filters:
                    return []
                return super().search(_query, limit, filters)

        service = AgenticAnswerService(
            retriever=EmptyPrimaryRetriever(),
            reranker=lambda _query, documents: documents,
            generator=lambda *_args: "생성되면 안 됨",
        )
        response = service.answer("Q-alt", "기업A의 2024년 사업보고서 매출은?")
        self.assertIn("이유:", response.answer)
        self.assertIn("a-2023.pdf", response.answer)
        self.assertIn("b-2024.pdf", response.answer)
        self.assertIn("직접적인 근거로 사용하지 않았습니다", response.answer)
        self.assertNotIn("a-2023.pdf", response.retrieved_context)
