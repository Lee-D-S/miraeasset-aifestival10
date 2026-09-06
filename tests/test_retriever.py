from __future__ import annotations

import copy

from retriever import InMemoryRetriever, RetrievalConfig, build_retriever_node
from integration.supervisor import retry_search_tool
from retriever.retrieval import matches_manifest_filter, build_search_query


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


def test_retriever_merges_keyword_and_vector_and_mirrors_citations():
    retriever = InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0})
    node = build_retriever_node(retriever=retriever, config=RetrievalConfig(final_limit=2))
    state = _state()
    state["intent"]["manifest_filter"]["base_years"] = [2024, 2025]
    original = copy.deepcopy(state)

    update = node(state)

    assert state == original
    result = update["retriever_result"]
    assert result["status"] == "ok"
    assert result["cited_documents"][0]["id"] == "samsung-2025-revenue"
    assert [item["id"] for item in result["cited_documents"]] == [
        "samsung-2025-revenue",
        "samsung-2024-revenue",
    ]
    assert result["cited_documents"][0]["match_sources"] == ["keyword", "vector"]
    assert update["documents"] == result["cited_documents"]
    assert "reasoner_result" not in update
    assert "answer" not in update


def test_blocked_route_skips_backend_and_returns_structured_result():
    class FailingRetriever:
        def filter_candidates(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

        def keyword_search(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

        def vector_search(self, *_args, **_kwargs):
            raise AssertionError("blocked route must not search")

    result = build_retriever_node(retriever=FailingRetriever())({**_state(), "route": "unsafe"})["retriever_result"]
    assert result["status"] == "skipped"
    assert result["documents"] == []
    assert result["cited_documents"] == []


def test_empty_search_returns_not_found():
    result = build_retriever_node(retriever=InMemoryRetriever([]))(_state())["retriever_result"]
    assert result["status"] == "not_found"
    assert result["documents"] == []
    assert result["cited_documents"] == []


def test_retriever_search_query_uses_korean_metric_labels():
    query = build_search_query(
        "삼성전자의 2025년 부채비율은?",
        {
            "normalized_question": "삼성전자의 2025년 부채비율은?",
            "metric": "total_assets",
            "basis": "연결",
            "time": {"years": [2025]},
        },
    )
    assert "부채비율" in query
    assert "total_assets" not in query


def test_retriever_search_query_includes_requested_years():
    query = build_search_query(
        "삼성전자의 최근 3년 매출액을 알려줘",
        {
            "normalized_question": "삼성전자의 최근 3년 매출액을 알려줘",
            "metric": "revenue",
            "time": {"years": [2023, 2024, 2025]},
        },
    )
    assert "2023" in query and "2025" in query
    assert "당사의 매출" in query


def test_retriever_diversify_by_year_keeps_each_requested_year():
    from retriever.retrieval import _diversify_by_year

    docs = [
        {"id": "y25", "metadata": {"base_year": 2025}, "hybrid_score": 0.2},
        {"id": "y24a", "metadata": {"base_year": 2024}, "hybrid_score": 0.9},
        {"id": "y24b", "metadata": {"base_year": 2024}, "hybrid_score": 0.8},
        {"id": "y23", "metadata": {"base_year": 2023}, "hybrid_score": 0.1},
    ]
    picked = _diversify_by_year(docs, ["2023", "2024", "2025"], 3)
    assert {item["id"] for item in picked} == {"y25", "y24a", "y23"}


def test_retriever_diversify_prefers_company_total_over_plan_table():
    from retriever.retrieval import _diversify_by_year

    docs = [
        {
            "id": "plan-2025",
            "text": "|  | 2025년(당기 이행연도) | 잔여 계획기간 합계 | 합계 구간  합계 |",
            "metadata": {"base_year": 2025},
            "hybrid_score": 0.99,
        },
        {
            "id": "total-2025",
            "text": "2025년 당사의 매출은 333조 6,059억원으로 전년 동기 대비 증가하였으며...",
            "metadata": {"base_year": 2025},
            "hybrid_score": 0.1,
        },
        {
            "id": "total-2024",
            "text": "2024년 당사의 매출은 300조 8,709억원으로 전년 동기 대비 증가하였으며...",
            "metadata": {"base_year": 2024},
            "hybrid_score": 0.5,
        },
        {
            "id": "total-2023",
            "text": "2023년 당사의 매출은 258조 9,355억원으로 전년 대비 감소하였으며...",
            "metadata": {"base_year": 2023},
            "hybrid_score": 0.4,
        },
    ]
    picked = _diversify_by_year(docs, ["2023", "2024", "2025"], 10, metric="revenue")
    by_year = {str(item["metadata"]["base_year"]): item["id"] for item in picked[:3]}
    assert by_year["2025"] == "total-2025"
    assert {item["id"] for item in picked} >= {"total-2023", "total-2024", "total-2025"}


def test_retriever_diversify_keeps_top_hit_for_non_revenue_metric():
    """The company-total promotion is revenue-only; operating_profit keeps the
    top-scored chunk of each year (an income-statement table row), not a
    '당사의 매출' sentence."""
    from retriever.retrieval import _diversify_by_year

    docs = [
        {
            "id": "income-2025",
            "text": "| 영업이익 | 42,180,000 | 32,725,961 |",
            "metadata": {"base_year": 2025},
            "hybrid_score": 0.99,
        },
        {
            "id": "narrative-2025",
            "text": "2025년 당사의 매출은 333조 6,059억원으로 영업이익은 42조원을 기록하였으며...",
            "metadata": {"base_year": 2025},
            "hybrid_score": 0.1,
        },
        {
            "id": "income-2024",
            "text": "| 영업이익 | 32,725,961 | 6,566,976 |",
            "metadata": {"base_year": 2024},
            "hybrid_score": 0.7,
        },
        {
            "id": "income-2023",
            "text": "| 영업이익 | 6,566,976 | 43,376,630 |",
            "metadata": {"base_year": 2023},
            "hybrid_score": 0.6,
        },
    ]
    picked = _diversify_by_year(docs, ["2023", "2024", "2025"], 10, metric="operating_profit")
    by_year = {str(item["metadata"]["base_year"]): item["id"] for item in picked[:3]}
    assert by_year["2025"] == "income-2025"


def test_search_by_year_injects_summary_table_for_operating_profit():
    from retriever.retrieval import _search_results_by_year

    # The per-year keyword hit is a prose delta chunk; the 요약재무정보 table
    # chunk (with per-row JSON) sits in the candidate subset but ranks below it.
    candidates = [
        {
            "id": "prose-2023",
            "text": "제55기 영업이익은 전년 대비 36조원 감소하였으며",
            "metadata": {"base_year": 2023},
        },
        {
            "id": "summary-2023",
            "text": "| 구 분 | 제55기 | 제54기 | 제53기 |\n| 영업이익 | 6,566,976 | 43,376,630 | 51,633,856 |",
            "raw_json_content": '[{"구 분": "영업이익", "제55기": "6,566,976"}]',
            "metadata": {"base_year": 2023, "section_name": "1. 요약재무정보"},
        },
    ]

    def keyword_search(query, docs, limit):
        # deterministic: only the prose chunk "matches", summary chunk is missed
        return [d for d in docs if "영업이익은" in d["text"]][:limit]

    hits = _search_results_by_year(
        keyword_search, "삼성전자 최근 3년 영업이익 추이", candidates,
        ["2023", "2024", "2025"], 6, metric="operating_profit",
    )
    ids = {d["id"] for d in hits}
    assert "summary-2023" in ids  # force-included despite missing the keyword hit

    # revenue keeps the existing behaviour: no summary-table injection
    hits_rev = _search_results_by_year(
        keyword_search, "삼성전자 최근 3년 매출액 추이", candidates,
        ["2023", "2024", "2025"], 6, metric="revenue",
    )
    assert "summary-2023" not in {d["id"] for d in hits_rev}


def test_retriever_keeps_yearly_totals_when_plan_tables_dominate_candidates():
    plan_tables = [
        {
            "id": f"plan-2025-{index}",
            "text": (
                "삼성전자 연결 매출액 | 2024년 | 2025년(당기 이행연도) | "
                "잔여 계획기간 합계 | 합계 구간  합계 |"
            ),
            "source": "plan.md",
            "metadata": {
                "corp_name": "삼성전자",
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_year": 2025,
                "base_month": 12,
                "is_correction": False,
            },
        }
        for index in range(12)
    ]
    totals = [
        {
            "id": "total-2023",
            "text": "2023년 당사의 매출은 258조 9,355억원으로 전년 대비 감소하였으며...",
            "source": "2023.md",
            "metadata": {
                "corp_name": "삼성전자",
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_year": 2023,
                "base_month": 12,
                "is_correction": False,
            },
        },
        {
            "id": "total-2024",
            "text": "2024년 당사의 매출은 300조 8,709억원으로 전년 동기 대비 증가하였으며...",
            "source": "2024.md",
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
            "id": "total-2025",
            "text": "2025년 당사의 매출은 333조 6,059억원으로 전년 동기 대비 증가하였으며...",
            "source": "2025.md",
            "metadata": {
                "corp_name": "삼성전자",
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_year": 2025,
                "base_month": 12,
                "is_correction": False,
            },
        },
    ]
    documents = plan_tables + totals
    scores = {item["id"]: 0.2 for item in documents}
    scores.update({item["id"]: 0.9 for item in plan_tables})
    retriever = InMemoryRetriever(documents, vector_scores=scores)
    state = _state(
        question="삼성전자의 최근 3년 매출액 추이를 알려줘",
        intent={
            "normalized_question": "삼성전자의 최근 3년 매출액 추이를 알려줘",
            "metric": "revenue",
            "basis": "연결",
            "time": {"years": [2023, 2024, 2025], "base_months": [12]},
            "manifest_filter": {
                "corp_names": ["삼성전자"],
                "doc_group": "periodic",
                "doc_subtype": "annual",
                "base_years": [2023, 2024, 2025],
                "base_months": [12],
                "is_correction": False,
            },
        },
    )
    result = build_retriever_node(
        retriever=retriever,
        config=RetrievalConfig(candidate_limit=40, branch_limit=6, final_limit=6),
    )(state)["retriever_result"]
    cited = {item["id"] for item in result["documents"]}
    assert "total-2023" in cited
    assert "total-2024" in cited
    assert "total-2025" in cited


def test_reasoner_consumes_cited_documents_from_retriever_result():
    from reasoner.adapters.retriever import adapt_retriever_bundle

    retriever = InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0})
    result = build_retriever_node(retriever=retriever)(_state())["retriever_result"]
    bundle = adapt_retriever_bundle(result)
    assert [document.id for document in bundle.effective_documents()] == ["samsung-2025-revenue"]


def test_retriever_injects_reranker_and_records_query():
    class RecordingReranker:
        def __init__(self):
            self.calls = []

        def rerank(self, query, documents, limit):
            self.calls.append((query, [item["id"] for item in documents], limit))
            return list(reversed(documents))[:limit]

    reranker = RecordingReranker()
    node = build_retriever_node(
        retriever=InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0}),
        config=RetrievalConfig(final_limit=2, reranker=reranker),
    )
    update = node(_state())
    assert reranker.calls
    assert update["search_query"] == reranker.calls[0][0]
    assert len(update["retriever_result"]["cited_documents"]) <= 2


def test_retriever_limits_clova_candidates_and_records_suggestions():
    class RecordingReranker:
        def __init__(self):
            self.calls = []
            self.suggested_queries = ["대체 검색어"]
            self.last_provider_status = {"status": "observed", "operation": "reranker"}

        def rerank(self, query, documents, limit):
            self.calls.append((query, list(documents), limit))
            return list(documents)[:limit]

    reranker = RecordingReranker()
    state = _state()
    state["intent"]["manifest_filter"] = {}
    update = build_retriever_node(
        retriever=InMemoryRetriever(DOCUMENTS, vector_scores={
            "samsung-2025-revenue": 1.0,
            "samsung-2024-revenue": 0.9,
            "other-company": 0.8,
        }),
        config=RetrievalConfig(
            branch_limit=2,
            final_limit=2,
            reranker=reranker,
            reranker_candidate_limit=2,
        ),
    )(state)

    assert len(reranker.calls[0][1]) == 2
    assert reranker.calls[0][2] == 2
    assert "reranker_suggested_queries_count=1" in update["retriever_result"]["retrieval_trace"]


def test_retriever_reranker_failure_falls_back_to_deterministic_results():
    class FailingReranker:
        def rerank(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")

    state = _state()
    update = build_retriever_node(
        retriever=InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0}),
        config=RetrievalConfig(final_limit=1, reranker=FailingReranker()),
    )(state)
    result = update["retriever_result"]

    assert result["status"] == "ok"
    assert result["cited_documents"]
    assert result["warnings"] == ["reranker_fallback: RuntimeError"]
    assert result["provider_status"]["status"] == "provider_error"


def test_retry_search_changes_query_and_preserves_original_question():
    state = _state(original_question="original question", search_query="first search", retry_num=0)
    update = retry_search_tool(state)
    assert update["search_query"] != state["search_query"]
    assert state["original_question"] == "original question"
    assert update["search_attempts"] == 1


def test_retriever_executes_query_plan_sequentially_and_aggregates_documents():
    state = _state()
    base_filter = state["intent"]["manifest_filter"]
    state["intent"]["query_plan"] = [
        {
            "subquery_id": "subquery-1",
            "metric": "revenue",
            "question_type": "lookup",
            "calculation": {},
            "time": {"years": [2025]},
            "manifest_filter": base_filter,
        },
        {
            "subquery_id": "subquery-2",
            "metric": "operating_profit",
            "question_type": "lookup",
            "calculation": {},
            "time": {"years": [2025]},
            "manifest_filter": base_filter,
        },
    ]

    result = build_retriever_node(
        retriever=InMemoryRetriever(DOCUMENTS, vector_scores={"samsung-2025-revenue": 1.0}),
        config=RetrievalConfig(final_limit=2),
    )(state)["retriever_result"]

    assert result["status"] == "ok"
    assert [item["subquery_id"] for item in result["subresults"]] == ["subquery-1", "subquery-2"]
    assert len(result["subresults"]) == 2
    assert result["cited_documents"]
