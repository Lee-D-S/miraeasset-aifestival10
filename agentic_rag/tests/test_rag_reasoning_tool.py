import unittest

from agentic_rag.agents.answer_generator import make_answer_generator
from agentic_rag.llm.rag_reasoning_client import RagReasoningClient
from agentic_rag.service import AgenticAnswerService


class RagReasoningToolTests(unittest.TestCase):
    def test_tool_call_round_trip_uses_approved_local_search(self):
        payloads = []

        def transport(payload):
            payloads.append(payload)
            if len(payloads) == 1:
                return {"message": {"role": "assistant", "content": "", "toolCalls": [{"id": "call-1", "type": "function", "function": {"name": "local_corpus_search", "arguments": {"query": "기업 비교"}}}]}}
            return {"message": {"role": "assistant", "content": "<doc-a>기업A의 매출은 100입니다.</doc-a>"}, "usage": {"totalTokens": 12}}

        client = RagReasoningClient(transport=transport, search=lambda query: [{"id": "doc-a", "source": "a.pdf", "text": "기업A의 매출은 100입니다."}])
        result = client.generate_grounded_answer("기업A와 기업B 비교", [{"id": "seed", "text": "초기 근거"}])

        self.assertIn("doc-a", payloads[1]["messages"][-1]["content"])
        self.assertEqual(result["answer"], "<doc-a>기업A의 매출은 100입니다.</doc-a>")
        self.assertEqual(client.calls, 2)
        self.assertEqual(payloads[0]["tools"][0]["function"]["name"], "local_corpus_search")

    def test_unapproved_function_is_rejected(self):
        def transport(_payload):
            return {"message": {"toolCalls": [{"id": "bad", "function": {"name": "web_search", "arguments": {"query": "x"}}}]}}

        client = RagReasoningClient(transport=transport, search=lambda _query: [])
        with self.assertRaises(ValueError):
            client.generate_grounded_answer("질문", [{"id": "doc", "text": "근거"}])

    def test_simple_lookup_does_not_call_rag_reasoning(self):
        calls = []

        class FakeRag:
            def generate_grounded_answer(self, *_args):
                calls.append(True)
                return {"answer": "생성 답변"}

        generate = make_answer_generator(rag_reasoning=FakeRag(), search_tool=lambda _query: [])
        result = generate({"intent": "lookup", "normalized_question": "조회", "cited_documents": [{"id": "d", "source": "d.pdf", "text": "근거"}], "messages": []})
        self.assertEqual(result["answer"], "[출처: d.pdf] 근거")
        self.assertEqual(calls, [])

    def test_tool_provenance_keeps_calls_documents_and_usage(self):
        class FakeRag:
            def generate_grounded_answer(self, *_args):
                return {"answer": "<doc-1> 근거 답변", "tool_calls": [{"id": "call-1"}], "documents": [{"id": "doc-1"}], "usage": {"totalTokens": 8}}

        generate = make_answer_generator(rag_reasoning=FakeRag(), search_tool=lambda _query: [])
        result = generate({"intent": "comparison", "normalized_question": "비교", "cited_documents": [{"id": "doc-1", "source": "a.pdf", "text": "근거"}, {"id": "doc-2", "source": "b.pdf", "text": "근거"}], "messages": []})
        details = result["provenance"][0]["details"]
        self.assertEqual(details["tool_calls"][0]["id"], "call-1")
        self.assertEqual(details["tool_document_ids"], ["doc-1"])
        self.assertEqual(details["usage"]["totalTokens"], 8)

    def test_complex_comparison_graph_uses_rag_reasoning_generator(self):
        calls = []

        class FakeRetriever:
            def corp_names(self):
                return ["기업A", "기업B"]

            def search(self, _query, _limit, filters):
                company = filters.get("corp_name", "기업A")
                return [{"id": company, "source": f"{company}.pdf", "text": f"{company} 매출 100", "score": 0.9, "metadata": filters}]

        class FakeRag:
            def generate_grounded_answer(self, question, documents):
                calls.append((question, [item["id"] for item in documents]))
                return {"answer": "인용 포함 비교 답변", "tool_calls": [{"id": "call-1"}]}

        service = AgenticAnswerService(retriever=FakeRetriever(), reranker=lambda _q, docs: docs, rag_reasoning=FakeRag())
        response = service.answer("Q-tool", "기업A와 기업B 비교")
        self.assertEqual(response.answer, "인용 포함 비교 답변")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1], ["기업A", "기업B"])


if __name__ == "__main__":
    unittest.main()
