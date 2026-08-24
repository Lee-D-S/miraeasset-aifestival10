import unittest

from langgraph_rag.routes import (
    route_after_evaluation,
    route_after_retrieve,
)
from langgraph_rag.nodes.evaluate_groundedness import _parse_evaluation
from langgraph_rag.nodes.fallback import make_fallback_node


class RouteTests(unittest.TestCase):
    def test_empty_retrieval_routes_to_fallback(self):
        self.assertEqual(route_after_retrieve({"retrieved_documents": []}), "no_documents")

    def test_grounded_answer_routes_to_finalize(self):
        self.assertEqual(route_after_evaluation({"groundedness": "grounded"}), "finalize")

    def test_natural_language_groundedness_is_normalized(self):
        result, _ = _parse_evaluation("The answer can be considered factually supported and grounded.")
        self.assertEqual(result, "grounded")

    def test_fallback_includes_alternative_documents(self):
        node = make_fallback_node(
            type("Finder", (), {
                "find": lambda self, question: {
                    "same_company": [{"source": "A-2024.xml", "metadata": {"report_period": "2024-12"}}],
                    "same_period": [],
                }
            })()
        )
        result = node({
            "question": "A기업 2025년",
            "fallback_reason": "2025년 문서가 없습니다.",
        })
        self.assertIn("A-2024.xml", result["answer"])
        self.assertEqual(result["status"], "fallback")


if __name__ == "__main__":
    unittest.main()
