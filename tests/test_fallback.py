import unittest

from common.fallback import format_fallback_answer
from common.schemas import RetrievedDocument
from rag.retrieval.alternatives import AlternativeFinder


class FallbackAnswerTests(unittest.TestCase):
    def test_reason_and_alternatives_are_separated_from_direct_answer(self):
        answer = format_fallback_answer(
            "2025년 A기업의 일치 문서가 없습니다.",
            same_company=[{
                "source": "A-2024.xml",
                "metadata": {"report_period": "2024-12"},
            }],
            same_period=[{
                "source": "B-2025.xml",
                "metadata": {"report_period": "2025-12"},
            }],
        )
        self.assertIn("2025년 A기업의 일치 문서가 없습니다.", answer)
        self.assertIn("A-2024.xml", answer)
        self.assertIn("B-2025.xml", answer)
        self.assertIn("직접적인 근거로 사용하지 않았습니다", answer)

    def test_alternative_finder_separates_company_and_period_candidates(self):
        class FakeRetriever:
            def list_corp_names(self):
                return ["A기업", "B기업"]

            def search(self, query, *, limit, filters):
                if filters == {"corp_name": "A기업"}:
                    return [
                        RetrievedDocument(id="a-2024-1", source="A-2024.xml", text="", score=0.8, metadata={"corp_name": "A기업", "report_period": "2024-12"}),
                        RetrievedDocument(id="a-2024-2", source="A-2024.xml", text="", score=0.7, metadata={"corp_name": "A기업", "report_period": "2024-12"}),
                    ]
                return [RetrievedDocument(id="b-2025", source="B-2025.xml", text="", score=0.7, metadata={"corp_name": "B기업", "report_period": "2025-12"})]

        result = AlternativeFinder(FakeRetriever()).find("A기업 2025년 사업보고서")
        self.assertEqual(result.same_company[0].id, "a-2024-1")
        self.assertEqual(len(result.same_company), 1)
        self.assertEqual(result.same_period[0].id, "b-2025")


if __name__ == "__main__":
    unittest.main()
