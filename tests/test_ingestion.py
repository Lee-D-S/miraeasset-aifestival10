from __future__ import annotations

import json

from stage2.ingestion import build_chunk_rows, chunk_text


def test_chunk_text_is_deterministic_and_bounded():
    chunks = chunk_text("one two three four five six", max_chars=10)
    assert chunks == chunk_text("one two three four five six", max_chars=10)
    assert all(len(chunk) <= 10 for chunk in chunks)


def test_selected_xml_is_read_and_metadata_is_preserved(tmp_path):
    document_dir = tmp_path / "data" / "doc-1"
    document_dir.mkdir(parents=True)
    (document_dir / "source.xml").write_text("<root><title>매출액</title><value>100억원</value></root>", encoding="utf-8")
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps({"documents": [{
        "doc_id": "doc-1",
        "stock_code": "005930",
        "base_year": 2025,
        "base_month": 12,
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "is_correction": False,
        "file_path": "data/doc-1",
    }]}), encoding="utf-8")

    rows = build_chunk_rows(selection, source_root=tmp_path, max_chars=100)

    assert len(rows) == 1
    assert "매출액" in rows[0]["text"]
    assert rows[0]["metadata"]["stock_code"] == "005930"
    assert rows[0]["metadata"]["base_year"] == 2025
    assert "embedding" not in rows[0]
