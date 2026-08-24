import unittest

from rag.generation.answer_generator import RagAnswerGenerator
from rag.retrieval.rerank import RerankedResult
from rag.schemas import RetrievedDocument


class FakeReasoningClient:
    def __init__(self):
        self.messages = []

    def generate(self, messages, tools):
        self.messages.append(messages)
        return {"message": {"content": "document-grounded answer", "toolCalls": []}}


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


if __name__ == "__main__":
    unittest.main()
