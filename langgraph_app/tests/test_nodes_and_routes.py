import unittest

from langgraph_app.routes import (
    route_after_evaluation,
    route_after_retrieve,
)
from langgraph_app.nodes.evaluate_groundedness import _parse_evaluation


class RouteTests(unittest.TestCase):
    def test_empty_retrieval_routes_to_fallback(self):
        self.assertEqual(route_after_retrieve({"retrieved_documents": []}), "no_documents")

    def test_grounded_answer_routes_to_finalize(self):
        self.assertEqual(route_after_evaluation({"groundedness": "grounded"}), "finalize")

    def test_natural_language_groundedness_is_normalized(self):
        result, _ = _parse_evaluation("The answer can be considered factually supported and grounded.")
        self.assertEqual(result, "grounded")


if __name__ == "__main__":
    unittest.main()
