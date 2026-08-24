import unittest

from rag.generation.answer_generator import RagAnswerGenerator
from rag.retrieval.rerank import RerankedResult
from common.schemas import RetrievedDocument


class FakeReasoningClient:
    def __init__(self):
        self.messages = []

    def generate(self, messages, tools):
        self.messages.append(messages)
        return {"message": {"content": "document-grounded answer", "toolCalls": []}}


class EmptyThenAnswerClient:
    def __init__(self):
        self.calls = 0

    def generate(self, messages, tools):
        self.calls += 1
        content = "" if self.calls == 1 else "retry answer"
        return {"message": {"content": content, "toolCalls": []}}


class AnswerGeneratorTests(unittest.TestCase):
    def test_initial_reranked_documents_are_sent_to_llm(self):
        client = FakeReasoningClient()
        document = RetrievedDocument(
            id="doc-1",
            source="sample.xml",
            text="initial reranked document content",
            score=0.9,
        )

        result = RagAnswerGenerator(client).generate(
            "question",
            lambda _: RerankedResult(answer="", documents=[]),
            initial_documents=[document],
        )

        self.assertIn("initial reranked document content", client.messages[0][0]["content"])
        self.assertEqual(result.documents, [document])

    def test_empty_model_answer_is_retried(self):
        client = EmptyThenAnswerClient()
        result = RagAnswerGenerator(client).generate("question", lambda _: RerankedResult(answer="", documents=[]))
        self.assertEqual(result.answer, "retry answer")
        self.assertEqual(client.calls, 2)


if __name__ == "__main__":
    unittest.main()
