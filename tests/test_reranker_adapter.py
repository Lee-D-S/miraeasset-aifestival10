from __future__ import annotations

import json

from integration.rate_limit import ClovaRateLimiter
from integration.reranker import ClovaRerankerClient


class _Response:
    headers = {}

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_reranker_payload_order_and_metadata_restore(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return _Response({
            "status": {"code": "20000"},
            "result": {
                "citedDocuments": [{"id": "doc-b"}, {"id": "unknown"}, {"id": "doc-a"}],
                "suggestedQueries": ["대체 검색어"],
            },
        })

    monkeypatch.setattr("integration.reranker.urlopen", fake_urlopen)
    client = ClovaRerankerClient(
        host="example.test",
        api_key="test-key",
        timeout=3,
        rate_limiter=ClovaRateLimiter(default_qpm=10, default_tpm=10000),
    )
    documents = [
        {"id": "doc-a", "text": "A", "metadata": {"year": 2025}},
        {"id": "doc-b", "text": "B", "metadata": {"year": 2024}},
    ]

    result = client.rerank("질의", documents, 2)

    assert captured["url"] == "https://example.test/v1/api-tools/reranker"
    assert captured["payload"] == {
        "documents": [{"id": "doc-a", "doc": "A"}, {"id": "doc-b", "doc": "B"}],
        "query": "질의",
        "maxTokens": 1024,
    }
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert [item["id"] for item in result] == ["doc-b", "doc-a"]
    assert result[0]["metadata"] == {"year": 2024}
    assert client.suggested_queries == ["대체 검색어"]


def test_reranker_rejects_response_with_no_known_document(monkeypatch):
    monkeypatch.setattr(
        "integration.reranker.urlopen",
        lambda *_args, **_kwargs: _Response({
            "status": {"code": "20000"},
            "result": {"citedDocuments": [{"id": "unknown"}]},
        }),
    )
    client = ClovaRerankerClient(
        api_key="test-key",
        rate_limiter=ClovaRateLimiter(default_qpm=10, default_tpm=10000),
    )

    try:
        client.rerank("질의", [{"id": "doc-a", "text": "A"}], 1)
    except ValueError as error:
        assert "known cited documents" in str(error)
    else:  # pragma: no cover
        raise AssertionError("unknown cited IDs must trigger retrieval fallback")
