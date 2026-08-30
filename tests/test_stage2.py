from __future__ import annotations

import copy

from stage2 import InMemoryRetriever, RetrievalConfig, build_stage2_node
from integration.supervisor import retry_search_tool
from stage2.retrieval import matches_manifest_filter


DOCUMENTS = [
    {
        "id": "samsung-2025-revenue",
        "text": "삼성전자 2025년 연결 기준 매출액은 300조원입니다.",
        "source": "samsung.xml",
        "metadata": {
            "corp_name": "삼성전자",
            "doc_group": "periodic",
            "doc_subtype": "annual",
            "base_year": 2025,
            "base_month": 12,
            "is_correction": False,
        },
    },
    {
        "id": "samsung-2024-revenue",
        "text": "삼성전자 2024년 연결 기준 매출액은 258조원입니다.",
        "source": "samsung-2024.xml",
        "metadata": {
            "corp_name": "삼성전자",
            "doc_group": "periodic",
            "doc_subtype": "annual",
            "base_year": 2024,
            "base_month": 12,
            "is_correction": False,
        },
    },
    {
        "id": "other-company",
        "text": "다른 기업의 2025년 매출액 공시입니다.",
        "source": "other.xml",
        "metadata": {
            "corp_name": "다른기업",
            "doc_group": "periodic",
            "doc_subtype": "annual",
            "base_year": 2025,
            "base_month": 12,
            "is_correction": False,
        },
    },
]


def _state(**overrides):
    state = {
        "question_id": "Q-001",
        "question": "삼성전자의 2025년 연결 기준 매출액은?",
        "route": "ok",
        "intent": {
            "normalized_question": "삼성전자 2025년 연결 기준 매출액",
            "metric": "revenue",
            "basis": "연결",
            "manifest_filter": {
                "corp_names": ["삼성전자"],
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_years": [2025],
                "base_months": [12],
                "is_correction": False,
            },
        },
        "retry_num": 0,
    }
    state.update(overrides)
    return state


def test_manifest_filter_applies_company_period_type_and_correction():
    manifest_filter = _state()["intent"]["manifest_filter"]
    assert matches_manifest_filter(DOCUMENTS[0], manifest_filter)
    assert not matches_manifest_filter(DOCUMENTS[1], manifest_filter)
    assert not matches_manifest_filter(DOCUMENTS[2], manifest_filter)


def test_stage2_merges_keyword_and_vector_and_mirrors_citations():
    retriever = InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0})
    node = build_stage2_node(retriever=retriever, config=RetrievalConfig(final_limit=2))
    state = _state()
    state["intent"]["manifest_filter"]["base_years"] = [2024, 2025]
    original = copy.deepcopy(state)

    update = node(state)

    assert state == original
    result = update["stage2_result"]
    assert result["status"] == "ok"
    assert result["cited_documents"][0]["id"] == "samsung-2025-revenue"
    assert [item["id"] for item in result["cited_documents"]] == [
        "samsung-2025-revenue",
        "samsung-2024-revenue",
    ]
    assert result["cited_documents"][0]["match_sources"] == ["keyword", "vector"]
    assert update["documents"] == result["cited_documents"]
    assert "stage3_result" not in update
    assert "answer" not in update


def test_blocked_route_skips_backend_and_returns_structured_result():
    class FailingRetriever:
        def filter_candidates(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

        def keyword_search(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

        def vector_search(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

    result = build_stage2_node(retriever=FailingRetriever())({**_state(), "route": "unsafe"})["stage2_result"]
    assert result["status"] == "skipped"
    assert result["documents"] == []
    assert result["cited_documents"] == []


def test_empty_search_returns_not_found():
    result = build_stage2_node(retriever=InMemoryRetriever([]))(_state())["stage2_result"]
    assert result["status"] == "not_found"
    assert result["documents"] == []
    assert result["cited_documents"] == []


def test_stage3_consumes_cited_documents_from_stage2_result():
    from stage3.adapters.stage2 import adapt_stage2_bundle

    retriever = InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0})
    result = build_stage2_node(retriever=retriever)(_state())["stage2_result"]
    bundle = adapt_stage2_bundle(result)
    assert [document.id for document in bundle.effective_documents()] == ["samsung-2025-revenue"]


def test_stage2_injects_reranker_and_records_query():
    class RecordingReranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, documents, limit):
            self.calls.append((query, [item["id"] for item in documents], limit))
            return list(reversed(documents))[:limit]

    reranker = RecordingReranker()
    node = build_stage2_node(
        retriever=InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0}),
        config=RetrievalConfig(final_limit=2, reranker=reranker),
    )
    update = node(_state())
    assert reranker.calls
    assert update["search_query"] == reranker.calls[0][0]
    assert len(update["stage2_result"]["cited_documents"]) <= 2


def test_retry_search_changes_query_and_preserves_original_question():
    state = _state(original_question="original question", search_query="first search", retry_num=0)
    update = retry_search_tool(state)
    assert update["search_query"] != state["search_query"]
    assert state["original_question"] == "original question"
    assert update["search_attempts"] == 1
