from __future__ import annotations

from types import SimpleNamespace

import integration.composition as composition


def test_stage1_and_reranker_flags_are_independent(monkeypatch):
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
    monkeypatch.setattr(composition.config.Stage2Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(composition, "validate_environment", lambda _mode: [])
    monkeypatch.setattr(composition, "_build_retriever", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(composition, "ClovaChatClient", FakeChat)
    monkeypatch.setattr(composition, "ClovaRerankerClient", FakeReranker)
    def fake_stage1(**kwargs):
        captured["stage1"] = kwargs
        return lambda _state: {}

    def fake_stage2(**kwargs):
        captured["stage2"] = kwargs
        return lambda _state: {}

    def fake_stage4(**kwargs):
        captured["stage4"] = kwargs
        return lambda _state: {}

    monkeypatch.setattr(composition, "build_stage1_node", fake_stage1)
    monkeypatch.setattr(composition, "build_stage2_node", fake_stage2)
    monkeypatch.setattr(composition, "build_stage3_node", lambda **kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_stage4_node", fake_stage4)
    monkeypatch.setattr(composition, "AnswerWriter", lambda _client: object())
    monkeypatch.setenv("CLOVA_LLM_ENABLED", "false")
    monkeypatch.setenv("STAGE1_USE_LLM", "1")
    monkeypatch.setenv("CLOVA_RERANKER_ENABLED", "true")

    composition.build_pipeline()

    assert captured["stage1"]["use_llm"] is True
    assert captured["stage1"]["llm_client"] is not None
    assert captured["stage4"]["validator_client"] is None
    assert captured["stage2"]["config"].reranker is not None
    assert captured["stage2"]["config"].reranker_candidate_limit == 100
    assert captured["stage2"]["config"].final_limit == 100
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
    monkeypatch.setattr(composition.config.Stage2Settings, "from_env", staticmethod(lambda: settings))
    monkeypatch.setattr(composition, "validate_environment", lambda _mode: [])
    monkeypatch.setattr(composition, "_build_retriever", lambda *_args, **_kwargs: object())

    def fail_if_constructed(**_kwargs):
        raise AssertionError("CLOVA client must not be constructed")

    monkeypatch.setattr(composition, "ClovaChatClient", fail_if_constructed)
    monkeypatch.setattr(composition, "ClovaRerankerClient", fail_if_constructed)
    monkeypatch.setattr(composition, "build_stage1_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_stage2_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_stage3_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.setattr(composition, "build_stage4_node", lambda **_kwargs: lambda _state: {})
    monkeypatch.delenv("CLOVA_LLM_ENABLED", raising=False)
    monkeypatch.delenv("STAGE1_USE_LLM", raising=False)
    monkeypatch.delenv("CLOVA_RERANKER_ENABLED", raising=False)

    composition.build_pipeline()
