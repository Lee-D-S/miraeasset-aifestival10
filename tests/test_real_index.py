from __future__ import annotations

import pytest

from scripts.check_real_index import run_checks


pytestmark = pytest.mark.real_index


def test_supplied_index_and_pipeline_are_healthy():
    report = run_checks(allow_partial_index=True)
    assert report["read_only"] is True
    assert report["embedding_model"] == "intfloat/multilingual-e5-large"
    assert report["sqlite_row_count"] > 1_000_000
    assert report["chroma_collection_count"] > 800_000
    assert report["hnsw_index_count"] > 0
    assert report["embedding_dimension"] == 1024
    assert len(report["searches"]) == 2
    assert len(report["pipeline"]) == 5
