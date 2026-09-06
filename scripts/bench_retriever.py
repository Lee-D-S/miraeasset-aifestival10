#!/usr/bin/env python3
"""Retriever 검색 시간 스모크 벤치마크.

측정 항목
  Part A : 전체 /answer 실행 시간 중 Retriever 검색(retrieve)이 차지하는 비중
  Part B : 같은 후보군에서 "키워드만 / 벡터만 / 둘 다(합집합)" 의 시간 차이

실행 (서버, dis-164 디렉터리에서)
  docker compose exec -T app python - < scripts/bench_retriever.py

환경변수
  BENCH_REPEAT_A  Part A 질문별 반복 (기본 3)   -- 매회 CLOVA chat/semantic 호출
  BENCH_REPEAT_B  Part B 질문별·방식별 반복 (기본 8)
  BENCH_QUESTIONS 세미콜론으로 구분한 질문 목록 (기본: 아래 5개)
"""

from __future__ import annotations

import os
import statistics
import time
from collections.abc import Mapping

REPEAT_A = int(os.getenv("BENCH_REPEAT_A", "3"))
REPEAT_B = int(os.getenv("BENCH_REPEAT_B", "8"))

_DEFAULT_QUESTIONS = [
    "SK하이닉스의 2024년 연결기준 매출액은 얼마인가?",
    "한화에어로스페이스의 2024년 대비 2025년 매출 증감률은?",
    "삼성전자의 2024년 영업이익 대비 연구개발비 비중은?",
    "셀트리온이 2024년에 실시한 자금조달 내역을 유형별로 정리해줘",
    "카카오의 2025년 사업보고서 기준 매출액은?",
]
QUESTIONS = [
    q.strip()
    for q in (os.getenv("BENCH_QUESTIONS", "").split(";") or [])
    if q.strip()
] or _DEFAULT_QUESTIONS


def stat(xs: list[float]) -> str:
    if not xs:
        return "n/a"
    xs = sorted(xs)
    p90 = xs[min(len(xs) - 1, int(round(len(xs) * 0.9)) - 1 if len(xs) > 1 else 0)]
    return (
        f"median={statistics.median(xs) * 1000:7.0f}ms  "
        f"mean={statistics.fmean(xs) * 1000:7.0f}ms  "
        f"p90={p90 * 1000:7.0f}ms  n={len(xs)}"
    )


# --------------------------------------------------------------------------
# 공유 retriever 클래스에 타이밍 후크. Part A(파이프라인 내부)와 Part B(직접 호출)
# 모두 같은 인스턴스를 지나므로 한 번만 설치한다.
# --------------------------------------------------------------------------
CALLS: dict[str, list[float]] = {"sql_filter": [], "keyword": [], "vector": []}


def _hook(cls, method: str, bucket: str) -> None:
    original = getattr(cls, method)

    def wrapped(self, *args, **kwargs):
        start = time.perf_counter()
        try:
            return original(self, *args, **kwargs)
        finally:
            CALLS[bucket].append(time.perf_counter() - start)

    wrapped.__name__ = method
    setattr(cls, method, wrapped)


from retriever.local_store import LocalHybridRetriever  # noqa: E402

_hook(LocalHybridRetriever, "filter_candidates", "sql_filter")
_hook(LocalHybridRetriever, "keyword_search", "keyword")
_hook(LocalHybridRetriever, "vector_search", "vector")

# retrieve() 전체 시간 기록 (재시도/멀티쿼리 포함).
import retriever.retrieval as _R  # noqa: E402

RETRIEVE_TOTAL: list[float] = []
_orig_retrieve = _R.retrieve


def _timed_retrieve(*args, **kwargs):
    start = time.perf_counter()
    try:
        return _orig_retrieve(*args, **kwargs)
    finally:
        RETRIEVE_TOTAL.append(time.perf_counter() - start)


_R.retrieve = _timed_retrieve
import retriever.node as _SN  # noqa: E402

_SN.retrieve = _timed_retrieve  # 노드가 이름으로 import 했으므로 여기도 교체

# --------------------------------------------------------------------------
# 파이프라인이 만드는 retriever 인스턴스를 가로채서 Part B 에서 재사용 (인덱스 1회 로드).
# --------------------------------------------------------------------------
import integration.composition as _COMP  # noqa: E402

_CAPTURED: dict[str, object] = {}
_orig_build_retriever = _COMP._build_retriever


def _capture_build_retriever(*args, **kwargs):
    retriever = _orig_build_retriever(*args, **kwargs)
    _CAPTURED["retriever"] = retriever
    return retriever


_COMP._build_retriever = _capture_build_retriever

import config  # noqa: E402
from integration.composition import build_pipeline  # noqa: E402
from retriever import RetrievalConfig  # noqa: E402
from retriever.retrieval import DeterministicReranker, build_search_query  # noqa: E402

CFG = RetrievalConfig(candidate_limit=1000, branch_limit=100, final_limit=200)


print("=" * 78)
print("파이프라인 빌드 (콜드 스타트: HNSW 인덱스 로드 포함)")
print("=" * 78)
_t = time.perf_counter()
pipe = build_pipeline()
print(f"build_pipeline: {time.perf_counter() - _t:.1f}s")
retriever = _CAPTURED.get("retriever")
if retriever is None:
    print("경고: retriever 를 가로채지 못함 (RETRIEVER_MODE 가 local/container 가 아님). Part B 생략.")


# ==========================================================================
# Part A — 전체 실행 중 Retriever 검색 비중
# ==========================================================================
print()
print("=" * 78)
print(f"Part A · 전체 /answer 대비 Retriever retrieve 비중  (질문당 {REPEAT_A}회)")
print("=" * 78)

intents: dict[str, dict] = {}
ratios: list[float] = []
totals: list[float] = []
s2_times: list[float] = []

for qi, question in enumerate(QUESTIONS):
    # 워밍업 1회 (측정 제외)
    try:
        pipe.invoke(question_id=f"warm-{qi}", question=question)
    except Exception as error:  # noqa: BLE001
        print(f"[{qi}] 워밍업 실패: {type(error).__name__}: {error}")
    for rep in range(REPEAT_A):
        RETRIEVE_TOTAL.clear()
        start = time.perf_counter()
        try:
            state = pipe.invoke(question_id=f"benchA-{qi}-{rep}", question=question)
        except Exception as error:  # noqa: BLE001
            print(f"[{qi}] r{rep} 실패: {type(error).__name__}: {error}")
            continue
        total = time.perf_counter() - start
        retriever_retrieve = sum(RETRIEVE_TOTAL)
        totals.append(total)
        s2_times.append(retriever_retrieve)
        if total > 0:
            ratios.append(retriever_retrieve / total)
        intents.setdefault(question, state.get("intent") or {})
        print(
            f"[{qi}] r{rep}  total={total * 1000:8.0f}ms   "
            f"retriever_retrieve={retriever_retrieve * 1000:8.0f}ms   "
            f"비중={retriever_retrieve / total * 100:5.1f}%   "
            f"route={state.get('route')}  retrieve호출={len(RETRIEVE_TOTAL)}"
        )

print("-" * 78)
print(f"total           {stat(totals)}")
print(f"retriever_retrieve {stat(s2_times)}")
if ratios:
    print(
        f"비중            median={statistics.median(ratios) * 100:.1f}%   "
        f"mean={statistics.fmean(ratios) * 100:.1f}%   "
        f"범위={min(ratios) * 100:.1f}~{max(ratios) * 100:.1f}%"
    )


# ==========================================================================
# Part B — 방식별 (키워드만 / 벡터만 / 둘 다)
# ==========================================================================
if retriever is not None:
    print()
    print("=" * 78)
    print(f"Part B · 검색 방식별 시간  (동일 후보군, 방식·질문당 {REPEAT_B}회)")
    print("=" * 78)

    reranker = DeterministicReranker()

    def _variant(mode: str):
        """base retriever 를 감싸 한쪽 브랜치를 끈다."""

        class _V:
            def filter_candidates(self, manifest_filter, limit):
                return retriever.filter_candidates(manifest_filter, limit)

            def keyword_search(self, query, candidates, limit):
                if mode == "vector":
                    return []
                return retriever.keyword_search(query, candidates, limit)

            def vector_search(self, query, candidates, limit):
                if mode == "keyword":
                    return []
                return retriever.vector_search(query, candidates, limit)

        return _V()

    for qi, question in enumerate(QUESTIONS):
        intent = intents.get(question) or {}
        manifest_filter = intent.get("manifest_filter")
        if not isinstance(manifest_filter, Mapping) or not manifest_filter:
            print(f"[{qi}] {question[:44]}  → manifest_filter 없음 (route={intent.get('route')}), 생략")
            continue
        query = build_search_query(question, intent)

        # 워밍업
        for mode in ("keyword", "vector", "both"):
            try:
                _orig_retrieve(
                    question_id="warm", question=question, intent=intent, route="ok",
                    retriever=_variant(mode), config=CFG, search_query=query,
                )
            except Exception:  # noqa: BLE001
                pass

        times: dict[str, list[float]] = {"keyword": [], "vector": [], "both": []}
        sql_only: dict[str, list[float]] = {"keyword": [], "vector": [], "both": []}
        cited: dict[str, int] = {}
        for rep in range(REPEAT_B):
            for mode in ("keyword", "vector", "both"):
                for bucket in CALLS.values():
                    bucket.clear()
                start = time.perf_counter()
                try:
                    result = _orig_retrieve(
                        question_id=f"benchB-{qi}", question=question, intent=intent,
                        route="ok", retriever=_variant(mode), config=CFG, search_query=query,
                    )
                except Exception as error:  # noqa: BLE001
                    print(f"[{qi}] {mode} 실패: {type(error).__name__}: {error}")
                    continue
                times[mode].append(time.perf_counter() - start)
                sql_only[mode].append(sum(CALLS["sql_filter"]))
                cited[mode] = len(result.get("cited_documents", []))

        print(f"\n[{qi}] {question}")
        print(f"      인용 문서수: keyword={cited.get('keyword')}  vector={cited.get('vector')}  both={cited.get('both')}")
        print(f"      SQL 필터(공통) {stat(sql_only['both'])}")
        print(f"      키워드만       {stat(times['keyword'])}")
        print(f"      벡터만         {stat(times['vector'])}")
        print(f"      둘 다(합집합)  {stat(times['both'])}")
        if times["keyword"] and times["vector"] and times["both"]:
            mk = statistics.median(times["keyword"])
            mv = statistics.median(times["vector"])
            mb = statistics.median(times["both"])
            slower = max(mk, mv)
            print(
                f"      → 합집합은 느린 단일 방식 대비 +{(mb - slower) * 1000:.0f}ms "
                f"({mb / slower:.2f}x), 두 방식 합({(mk + mv) * 1000:.0f}ms) 대비 "
                f"{mb / (mk + mv):.2f}x"
            )

print()
print("완료.")
