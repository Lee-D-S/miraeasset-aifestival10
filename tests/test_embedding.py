from __future__ import annotations

import sys
from types import SimpleNamespace

import integration.composition as composition
from stage2.embedding import E5InstructEmbeddings, QUERY_INSTRUCTION, format_e5_query


def test_e5_instruct_adapter_formats_queries_and_keeps_documents_raw(monkeypatch):
    calls = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, *, device, local_files_only):
            calls["model_name"] = model_name
            calls["device"] = device
            calls["local_files_only"] = local_files_only

        def encode(self, texts, **kwargs):
            calls.setdefault("text_calls", []).append(list(texts))
            calls["kwargs"] = kwargs
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setitem(
        sys.modules,
        "sentence_transformers",
        SimpleNamespace(SentenceTransformer=FakeSentenceTransformer),
    )

    embeddings = E5InstructEmbeddings(device="cpu")
    assert embeddings.embed_query("삼성전자 매출액") == [1.0, 0.0]
    assert embeddings.embed_documents(["문서 A", "문서 B"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert calls["model_name"] == "intfloat/multilingual-e5-large-instruct"
    assert calls["device"] == "cpu"
    assert calls["local_files_only"] is True
    assert calls["text_calls"] == [
        [f"Instruct: {QUERY_INSTRUCTION}\nQuery: 삼성전자 매출액"],
        ["문서 A", "문서 B"],
    ]
    assert calls["kwargs"] == {
        "normalize_embeddings": True,
        "convert_to_numpy": True,
        "show_progress_bar": False,
    }


def test_e5_query_format_is_explicit():
    assert format_e5_query("삼성전자 매출액") == (
        f"Instruct: {QUERY_INSTRUCTION}\nQuery: 삼성전자 매출액"
    )


def test_e5_instruct_adapter_allows_raw_baseline_with_injected_model():
    calls = []

    class FakeModel:
        def encode(self, texts, **kwargs):
            calls.append((list(texts), kwargs))
            return [[1.0, 0.0] for _ in texts]

    embeddings = E5InstructEmbeddings(model=FakeModel(), query_instruction=None)
    embeddings.embed_query("삼성전자 매출액")
    assert calls[0][0] == ["삼성전자 매출액"]


def test_composition_keeps_raw_until_safety_gate_passes(monkeypatch):
    calls = {}

    class FakeEmbeddings:
        def __init__(self, **kwargs):
            calls.update(kwargs)

    monkeypatch.setattr(composition, "E5InstructEmbeddings", FakeEmbeddings)
    embedding = composition._embedding_function(SimpleNamespace(embedding="e5-instruct"))

    assert isinstance(embedding, FakeEmbeddings)
    assert calls == {"query_instruction": None}
