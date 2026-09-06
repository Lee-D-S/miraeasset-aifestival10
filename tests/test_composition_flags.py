from __future__ import annotations

from types import SimpleNamespace

import integration.composition as composition


def test_interpreter_and_reranker_flags_are_independent(monkeypatch):
    captured = {}

    class FakeChat:
        def __init__(self, **kwargs):
            captured["chat_limiter"] = kwargs["rate_limiter"]

    class FakeReranker:
        def __init__(self, **kwargs):
            captured["reranker_limiter"] = kwargs["rate_limiter"]

    settings = SimpleNamespace(
        mode="local",
        embedding="e5",
        sqlite_path=None,
        chroma_path=None,
        sqlite_table="chunk_index",
        chroma_collection="chunk_vectors",
        allow_partial_index=False,
    )
    monkeypatch.setattr(composition.config.RetrieverSettings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(composition, "validate_environment", lambda _mode: [])
    monkeypatch.setattr(composition, "_build_retriever", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(composition, "ClovaChatClient", FakeChat)
    monkeypatch.setattr(composition, "ClovaRerankerClient", FakeReranker)
    def fake_interpreter(**kwargs):
        captured["interpreter"] = kwargs
        return lambda _state: {}

    def fake_retriever(**kwargs):
        captured["retriever"] = kwargs
        return lambda _state: {}

    def fake_validator(**kwargs):
        captured["validator"] = kwargs
        return lambda _state: {}

    monkeypatch.setattr(composition, "build_interpreter_node", fake_interpreter)
    monkeypatch.setattr(composition, "build_retriever_node", fake_retriever)
    monkeypatch.setattr(composition, "build_reasoner_node", lambda **kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_validator_node", fake_validator)
    monkeypatch.setattr(composition, "AnswerWriter", lambda _client: object())
    monkeypatch.setenv("CLOVA_LLM_ENABLED", "false")
    monkeypatch.setenv("STAGE1_USE_LLM", "1")
    monkeypatch.setenv("CLOVA_RERANKER_ENABLED", "true")

    composition.build_pipeline()

    assert captured["interpreter"]["use_llm"] is True
    assert captured["interpreter"]["llm_client"] is not None
    assert captured["validator"]["validator_client"] is None
    assert captured["retriever"]["config"].reranker is not None
    assert captured["retriever"]["config"].reranker_candidate_limit == 100
    assert captured["retriever"]["config"].final_limit == 100
    assert captured["chat_limiter"] is captured["reranker_limiter"]


def test_clova_capabilities_are_not_constructed_when_flags_are_off(monkeypatch):
    settings = SimpleNamespace(
        mode="local",
        embedding="e5",
        sqlite_path=None,
        chroma_path=None,
        sqlite_table="chunk_index",
        chroma_collection="chunk_vectors",
        allow_partial_index=False,
    )
    monkeypatch.setattr(composition.config.RetrieverSettings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(composition, "validate_environment", lambda _mode: [])
    monkeypatch.setattr(composition, "_build_retriever", lambda *_args, **_kwargs: object())

    def fail_if_constructed(**_kwargs):
        raise AssertionError("CLOVA client must not be constructed")

    monkeypatch.setattr(composition, "ClovaChatClient", fail_if_constructed)
    monkeypatch.setattr(composition, "ClovaRerankerClient", fail_if_constructed)
    monkeypatch.setattr(composition, "build_interpreter_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_retriever_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_reasoner_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_validator_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.delenv("CLOVA_LLM_ENABLED", raising=False)
    monkeypatch.delenv("STAGE1_USE_LLM", raising=False)
    monkeypatch.delenv("CLOVA_RERANKER_ENABLED", raising=False)

    composition.build_pipeline()
