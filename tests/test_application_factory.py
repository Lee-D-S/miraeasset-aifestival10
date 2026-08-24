import unittest

from application.factory import build_answer_service
from langgraph_app.service import LangGraphAnswerService
from rag.services.answer_service import AnswerService as ClassicAnswerService


class ApplicationFactoryTests(unittest.TestCase):
    def test_selects_classic_backend(self):
        self.assertIsInstance(build_answer_service("classic"), ClassicAnswerService)

    def test_selects_langgraph_backend(self):
        self.assertIsInstance(build_answer_service("langgraph"), LangGraphAnswerService)

    def test_rejects_unknown_backend(self):
        with self.assertRaises(ValueError):
            build_answer_service("unknown")


if __name__ == "__main__":
    unittest.main()
