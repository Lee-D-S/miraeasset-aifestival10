from __future__ import annotations

import json
from io import BytesIO
from urllib.error import HTTPError

from integration.clova import ClovaChatClient


def test_clova_chat_adapter_parses_text_and_json_without_network(monkeypatch):
    client = ClovaChatClient(api_key="test")
    responses = iter(["answer", json.dumps({"pass": True})])
    monkeypatch.setattr(client, "_request", lambda messages, **kwargs: next(responses))
    assert client.generate_text([]) == "answer"
    assert client.generate_json([], schema={}) == {"pass": True}


def test_clova_chat_adapter_bounds_rate_limit_wait_and_labels_operation(monkeypatch):
    client = ClovaChatClient(api_key="test", max_retries=1, timeout=1)
    waits = []

    def raise_rate_limit(*_args, **_kwargs):
        error = HTTPError("https://example.test", 429, "rate limit", {"Retry-After": "9999"}, BytesIO(b"{}"))
        raise error

    monkeypatch.setattr("integration.clova.urlopen", raise_rate_limit)
    monkeypatch.setattr("integration.clova.time.sleep", waits.append)

    try:
        client.generate_text([])
    except RuntimeError as error:
        assert "answer_generation HTTP 429" in str(error)
    else:  # pragma: no cover - the adapter must fail after the bounded retry
        raise AssertionError("expected a bounded retry failure")

    assert waits == [15.0]


def test_clova_chat_adapter_captures_rate_limit_headers(monkeypatch):
    client = ClovaChatClient(api_key="test", max_retries=0, timeout=1)

    class Response:
        headers = {
            "x-ratelimit-limit-requests": "90",
            "x-ratelimit-remaining-requests": "89",
            "x-ratelimit-reset-requests": "23s",
        }

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b'{"status": {"code": "20000"}, "result": {"message": {"content": "ok"}}}'

    monkeypatch.setattr("integration.clova.urlopen", lambda *_args, **_kwargs: Response())
    assert client.generate_text([]) == "ok"
    assert client.last_rate_limit["x-ratelimit-remaining-requests"] == "89"
