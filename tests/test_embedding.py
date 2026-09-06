from __future__ import annotations

from types import SimpleNamespace

import pytest

import config
import integration.composition as composition
from retriever.embedding import (
    E5Embeddings,
)
from retriever.ingestion.dart.embeddings import MODEL_NAME as DART_MODEL_NAME


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


def test_only_index_embedding_is_selectable():
    assert config.EMBEDDING == "e5"
    assert set(config.VALID_STAGE2_EMBEDDINGS) == {"e5"}


def test_unknown_embedding_lists_valid_choices():
    with pytest.raises(RuntimeError) as excinfo:
        composition._embedding_function(SimpleNamespace(embedding="bogus"))
    message = str(excinfo.value)
    assert "e5" in message and "e5-instruct" not in message


def test_instruct_embedding_is_rejected(monkeypatch):
    monkeypatch.setenv("STAGE2_EMBEDDING", "e5-instruct")
    from integration.readiness import validate_environment

    assert any("STAGE2_EMBEDDING" in issue for issue in validate_environment("local"))


def test_e5_adapter_model_matches_the_builder():
    """Serve-time and build-time must embed with the same model."""

    assert E5Embeddings.MODEL_NAME == "intfloat/multilingual-e5-large"
    assert E5Embeddings.MODEL_NAME == DART_MODEL_NAME


def test_e5_cache_failure_is_explicit(monkeypatch):
    def fail_to_load(**_kwargs):
        raise OSError("cache missing")

    monkeypatch.setattr(
        "retriever.ingestion.dart.embeddings.load_e5_model",
        fail_to_load,
    )
    with pytest.raises(RuntimeError, match="model cache is unavailable"):
        E5Embeddings()


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
        "retriever.ingestion.dart.embeddings.load_e5_model",
        lambda **_: FakeModel(),
    )
    adapter = E5Embeddings()

    assert adapter.embed_query("삼성전자 매출액") == [0.6, 0.8]  # L2-normalized
    assert adapter.embed_documents(["문서 A"]) == [[0.6, 0.8]]
    assert captured["query_embed"] == ["삼성전자 매출액"]
    assert captured["embed"] == ["문서 A"]
