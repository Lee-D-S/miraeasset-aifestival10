from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import create_engine

from integration.cache import CacheRegistry, TTLRUCache, build_index_signature
from stage2.embedding import E5Embeddings
from stage2.local_store import LocalHybridRetriever
from stage3.agents.fact_extraction import extract_facts
from stage3.contracts import Stage3Document, Stage3Intent
from stage3.parsing import structured


def _registry(**kwargs) -> CacheRegistry:
    defaults = {
        "enabled": True,
        "ttl_seconds": 600,
        "index_signature": "index-a",
        "query_embedding_max": 8,
        "structured_doc_max": 8,
        "fact_max": 8,
        "candidate_max": 8,
    }
    defaults.update(kwargs)
    return CacheRegistry(**defaults)


def _intent(*, metric: str = "revenue", basis: str | None = "연결") -> Stage3Intent:
    return Stage3Intent(
        question="삼성전자의 매출액은?",
        normalized_question="삼성전자 매출액",
        route="ok",
        intent="lookup",
        question_type="lookup",
        metric=metric,
        basis=basis,
        manifest_filter={"corp_names": ["삼성전자"]},
        companies=["삼성전자"],
    )


def _document(text: str = "삼성전자 매출액은 100억원입니다.") -> Stage3Document:
    return Stage3Document(
        id="chunk-1",
        source="report.xml",
        text=text,
        metadata={"corp_name": "삼성전자", "base_year": 2025},
    )


def test_ttlru_cache_hit_expiry_lru_and_mutation_isolation():
    now = [0.0]
    cache = TTLRUCache(max_size=2, ttl_seconds=10, clock=lambda: now[0])
    value = {"items": [1]}

    cache.put("a", value)
    value["items"].append(2)
    assert cache.get("a") == {"items": [1]}

    cache.put("b", "b")
    assert cache.get("a") == {"items": [1]}
    cache.put("c", "c")
    assert cache.get("b") is None
    assert cache.stats()["evictions"] == 1

    now[0] = 11.0
    assert cache.get("a") is None
    stats = cache.stats()
    assert stats["hits"] == 2
    assert stats["misses"] == 2


def test_cache_disabled_and_zero_capacity_are_bounded():
    disabled = CacheRegistry(enabled=False, index_signature="x")
    disabled.query_embeddings.put("q", [1.0])
    assert disabled.query_embeddings.get("q") is None

    zero = TTLRUCache(max_size=0, ttl_seconds=10)
    zero.put("q", [1.0])
    assert zero.get("q") is None


def test_registry_reads_cache_environment_defaults_and_overrides(monkeypatch):
    monkeypatch.setenv("DIS164_CACHE_ENABLED", "false")
    monkeypatch.setenv("DIS164_CACHE_TTL_SECONDS", "42")
    monkeypatch.setenv("DIS164_CACHE_QUERY_EMBEDDING_MAX", "3")
    monkeypatch.setenv("DIS164_CACHE_STRUCTURED_DOC_MAX", "4")
    monkeypatch.setenv("DIS164_CACHE_FACT_MAX", "5")
    monkeypatch.setenv("DIS164_CACHE_CANDIDATE_MAX", "6")

    registry = CacheRegistry.from_env(index_signature="index-env")
    assert registry.enabled is False
    assert registry.ttl_seconds == 42
    assert registry.query_embeddings.max_size == 3
    assert registry.structured_documents.max_size == 4
    assert registry.fact_results.max_size == 5
    assert registry.candidate_documents.max_size == 6


def test_index_signature_changes_with_version_or_index_metadata(tmp_path):
    sqlite_path = tmp_path / "index.db"
    chroma_path = tmp_path / "chroma"
    sqlite_path.write_text("index", encoding="utf-8")
    chroma_path.mkdir()

    first = build_index_signature(
        stage2_mode="local",
        sqlite_path=sqlite_path,
        chroma_path=chroma_path,
        index_version="1",
    )
    second = build_index_signature(
        stage2_mode="local",
        sqlite_path=sqlite_path,
        chroma_path=chroma_path,
        index_version="2",
    )
    assert first != second

    sqlite_path.write_text("changed index", encoding="utf-8")
    third = build_index_signature(
        stage2_mode="local",
        sqlite_path=sqlite_path,
        chroma_path=chroma_path,
        index_version="1",
    )
    assert first != third


def test_cache_is_thread_safe_for_concurrent_access():
    cache = TTLRUCache(max_size=32, ttl_seconds=600)

    def write_and_read(index: int) -> int:
        key = str(index)
        cache.put(key, {"value": index})
        return cache.get(key)["value"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(write_and_read, range(32)))
    assert values == list(range(32))


def test_pipeline_registries_are_isolated():
    first = _registry()
    second = _registry()
    first.query_embeddings.put("q", [1.0])
    assert second.query_embeddings.get("q") is None


def test_e5_query_embedding_cache_uses_final_query_and_index_signature(monkeypatch):
    calls = []

    class FakeModel:
        def query_embed(self, texts):
            calls.append(list(texts))
            return iter([[3.0, 4.0]])

        def embed(self, texts):
            return [[3.0, 4.0] for _ in texts]

    monkeypatch.setattr(
        "stage2.ingestion.dart.embeddings.load_e5_model",
        lambda **_: FakeModel(),
    )
    registry = _registry()
    adapter = E5Embeddings(cache=registry, index_signature="index-a")

    assert adapter.embed_query("최종 검색 질의") == [0.6, 0.8]
    assert adapter.embed_query("최종 검색 질의") == [0.6, 0.8]
    assert len(calls) == 1

    adapter_changed_index = E5Embeddings(cache=registry, index_signature="index-b")
    adapter_changed_index.embed_query("최종 검색 질의")
    assert len(calls) == 2

    disabled = _registry(enabled=False)
    uncached = E5Embeddings(cache=disabled, index_signature="index-a")
    uncached.embed_query("최종 검색 질의")
    uncached.embed_query("최종 검색 질의")
    assert len(calls) == 4


def test_local_sql_candidate_cache_reuses_only_same_filter_and_limit():
    calls = []
    repository = LocalHybridRetriever.__new__(LocalHybridRetriever)
    repository.table_name = "chunk_index"
    repository._cache = _registry()
    repository._index_signature = "index-a"
    repository.initialize = lambda: None

    def query_candidates(manifest_filter, limit):
        calls.append((dict(manifest_filter), limit))
        return [{"id": f"chunk-{len(calls)}", "text": "본문"}]

    repository._query_candidates = query_candidates
    manifest_filter = {"corp_names": ["삼성전자"], "base_years": [2025]}

    first = repository.filter_candidates(manifest_filter, 10)
    second = repository.filter_candidates(manifest_filter, 10)
    assert first == second
    assert len(calls) == 1

    repository.filter_candidates(manifest_filter, 11)
    repository._index_signature = "index-b"
    repository.filter_candidates(manifest_filter, 10)
    assert len(calls) == 3


def test_structured_parser_cache_reuses_same_chunk_text_and_version(monkeypatch):
    calls = []
    original = structured._markdown_rows

    def recording_parser(value):
        calls.append(value)
        return original(value)

    monkeypatch.setattr(structured, "_markdown_rows", recording_parser)
    registry = _registry()
    value = "| 항목 | 2025년 |\n| --- | --- |\n| 매출액 | 100억원 |"

    structured.parse_structured_evidence(value, cache=registry, document_id="chunk-1")
    structured.parse_structured_evidence(value, cache=registry, document_id="chunk-1")
    assert len(calls) == 1

    structured.parse_structured_evidence(value + " ", cache=registry, document_id="chunk-1")
    assert len(calls) == 2

    monkeypatch.setattr(structured, "STRUCTURED_PARSER_VERSION", "structured-parser-v2")
    structured.parse_structured_evidence(value, cache=registry, document_id="chunk-1")
    assert len(calls) == 3


def test_fact_cache_is_profile_specific_and_reuses_structured_parse(monkeypatch):
    calls = []
    import stage3.agents.fact_extraction as fact_module

    original = fact_module._extract_facts_uncached

    def recording_extractor(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(fact_module, "_extract_facts_uncached", recording_extractor)
    registry = _registry()
    document = _document()

    first = extract_facts([document], _intent(), cache=registry)
    second = extract_facts([document], _intent(), cache=registry)
    assert [fact.to_dict() for fact in first] == [fact.to_dict() for fact in second]
    assert len(calls) == 1

    changed_metric = extract_facts([document], _intent(metric="operating_profit"), cache=registry)
    assert changed_metric != first
    assert len(calls) == 2
    assert registry.fact_results.stats()["hits"] == 1
    assert registry.structured_documents.stats()["hits"] >= 1


def test_cache_on_and_off_produce_the_same_facts_and_cache_errors_bypass():
    document = _document()
    intent = _intent()
    cached = extract_facts([document], intent, cache=_registry())
    uncached = extract_facts([document], intent, cache=None)
    assert [fact.to_dict() for fact in cached] == [fact.to_dict() for fact in uncached]

    class BrokenCache:
        def get(self, _key):
            raise RuntimeError("cache read failed")

        def put(self, _key, _value):
            raise RuntimeError("cache write failed")

    class BrokenRegistry:
        fact_results = BrokenCache()
        structured_documents = BrokenCache()

    fallback = extract_facts([document], intent, cache=BrokenRegistry())
    assert [fact.to_dict() for fact in fallback] == [fact.to_dict() for fact in uncached]
