from __future__ import annotations

import json

from integration.clova import ClovaChatClient


def test_clova_chat_adapter_parses_text_and_json_without_network(monkeypatch):
    client = ClovaChatClient(api_key="test")
    responses = iter(["answer", json.dumps({"pass": True})])
    monkeypatch.setattr(client, "_request", lambda messages, **kwargs: next(responses))
    assert client.generate_text([]) == "answer"
    assert client.generate_json([], schema={}) == {"pass": True}
