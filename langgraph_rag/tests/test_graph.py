import unittest

from langgraph_rag.contracts import GraphDependencies
from langgraph_rag.checkpoint import build_memory_checkpointer
from langgraph_rag.graph import build_graph, render_mermaid


class FakeRetriever:
    def search(self, query: str, *, limit: int):
        return [{
            "id": "doc-1",
            "source": "sample.xml",
            "text": "삼성전자의 2023년 매출은 100억원입니다.",
            "score": 0.9,
            "metadata": {"corp_name": "삼성전자", "report_period": "2023-03"},
        }]


class FakeReranker:
    calls = []

    def rerank(self, query: str, documents):
        self.calls.append(list(documents))
        return {"answer": "근거 있음", "documents": list(documents), "suggested_queries": []}


class FakeLlm:
    prompts = []

    def generate(self, messages, tools):
        self.prompts.append(messages[-1].get("content", ""))
        if messages and "groundedness" in str(messages[-1].get("content", "")):
            return {"message": {"content": '{"groundedness":"grounded","reason":"document supports the answer"}'}}
        return {"message": {"content": "근거 기반 답변입니다."}}


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.reranker = FakeReranker()
        self.reranker.calls = []
        self.llm = FakeLlm()
        self.llm.prompts = []
        self.graph = build_graph(
            GraphDependencies(FakeRetriever(), self.reranker, self.llm),
            checkpointer=build_memory_checkpointer(),
        )

    def test_graph_compiles_and_contains_conditional_nodes(self):
        mermaid = render_mermaid(self.graph)
        for node in ("retrieve", "rerank", "generate_answer", "evaluate_groundedness", "fallback", "finalize"):
            self.assertIn(node, mermaid)

    def test_graph_runs_database_path(self):
        config = {"configurable": {"thread_id": "test-Q-1"}, "recursion_limit": 20}
        result = self.graph.invoke({
            "question_id": "Q-1",
            "question": "삼성전자 2023년 매출은?",
            "retry_count": 0,
            "max_retries": 1,
            "messages": [],
            "cited_documents": [],
            "trace": [],
        }, config=config)
        self.assertEqual(result["status"], "completed")

    def test_reranker_input_and_prompt_contract(self):
        result = self.graph.invoke({
            "question": "test",
            "retry_count": 0,
            "max_retries": 0,
            "messages": [],
            "cited_documents": [],
            "trace": [],
        }, config={"configurable": {"thread_id": "reranker-contract"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.reranker.calls[0]), 1)
        self.assertIn("공시 근거 문서", self.llm.prompts[0])
        self.assertEqual(result["answer"], "근거 기반 답변입니다.")


if __name__ == "__main__":
    unittest.main()
