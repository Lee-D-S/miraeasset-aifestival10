import unittest

from agentic_rag.infrastructure.postgres import PostgresVectorRetriever


class FakeCursor:
    description = [type("Column", (), {"name": name}) for name in ("id", "text", "source_path", "corp_name", "score")]

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, query, params): self.params = params
    def fetchall(self): return [("1", "근거", "doc.pdf", "기업A", 0.8)]
    def fetchone(self): return (1,)


class FakeConnection:
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def cursor(self): return FakeCursor()


class TestRetriever(PostgresVectorRetriever):
    def connection(self):
        class Context:
            def __enter__(self): return FakeConnection()
            def __exit__(self, *args): return False
        return Context()


class PostgresContractTests(unittest.TestCase):
    def test_search_contract_matches_local_shape(self):
        result = TestRetriever("dsn", lambda text: [0.1]).search("질문", 3, {})
        self.assertEqual(result[0]["id"], "1")
        self.assertEqual(result[0]["source"], "doc.pdf")
        self.assertEqual(result[0]["metadata"]["corp_name"], "기업A")

    def test_healthcheck_contract(self):
        self.assertTrue(TestRetriever("dsn", lambda text: [0.1]).healthcheck())
