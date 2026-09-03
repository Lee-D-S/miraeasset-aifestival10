from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import config
import integration.composition as composition
from stage2.embedding import (
    E5Embeddings,
    E5InstructEmbeddings,
    QUERY_INSTRUCTION,
    format_e5_query,
)
from stage2.ingestion.dart.embeddings import MODEL_NAME as DART_MODEL_NAME


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


def test_default_embedding_is_non_instruct_fastembed(monkeypatch):
    """STAGE2_EMBEDDING=e5 -> E5Embeddings (fastembed, non-instruct)."""

    made = []

    class FakeE5Embeddings:
        def __init__(self, **kwargs):
            made.append(kwargs)

    monkeypatch.setattr(composition, "E5Embeddings", FakeE5Embeddings)
    embedding = composition._embedding_function(SimpleNamespace(embedding="e5"))

    assert isinstance(embedding, FakeE5Embeddings)
    assert made == [{}]


def test_both_embedding_directions_stay_selectable():
    assert config.EMBEDDING == "e5"
    assert set(config.VALID_STAGE2_EMBEDDINGS) == {"e5", "e5-instruct"}


def test_unknown_embedding_lists_valid_choices():
    with pytest.raises(RuntimeError) as excinfo:
        composition._embedding_function(SimpleNamespace(embedding="bogus"))
    message = str(excinfo.value)
    assert "e5" in message and "e5-instruct" in message


def test_e5_adapter_model_matches_the_builder():
    """Serve-time and build-time must embed with the same model."""

    assert E5Embeddings.MODEL_NAME == "intfloat/multilingual-e5-large"
    assert E5Embeddings.MODEL_NAME == DART_MODEL_NAME


def test_e5_adapter_normalizes_and_uses_query_prefix(monkeypatch):
    captured = {}

    class FakeModel:
        def embed(self, texts):
            captured["embed"] = list(texts)
            return [[3.0, 4.0] for _ in texts]

        def query_embed(self, texts):
            captured["query_embed"] = list(texts)
            return iter([[3.0, 4.0]])

    monkeypatch.setattr(
        "stage2.ingestion.dart.embeddings.load_e5_model",
        lambda **_: FakeModel(),
    )
    adapter = E5Embeddings()

    assert adapter.embed_query("삼성전자 매출액") == [0.6, 0.8]  # L2-normalized
    assert adapter.embed_documents(["문서 A"]) == [[0.6, 0.8]]
    assert captured["query_embed"] == ["삼성전자 매출액"]
    assert captured["embed"] == ["문서 A"]
