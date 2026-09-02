from __future__ import annotations

import sys
from types import SimpleNamespace

from stage2.embedding import E5InstructEmbeddings


def test_e5_instruct_adapter_uses_raw_text_and_normalized_vectors(monkeypatch):
    calls = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, *, device, local_files_only):
            calls["model_name"] = model_name
            calls["device"] = device
            calls["local_files_only"] = local_files_only

        def encode(self, texts, **kwargs):
            calls["texts"] = texts
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
    assert calls["texts"] == ["문서 A", "문서 B"]
    assert calls["kwargs"] == {
        "normalize_embeddings": True,
        "convert_to_numpy": True,
        "show_progress_bar": False,
    }
