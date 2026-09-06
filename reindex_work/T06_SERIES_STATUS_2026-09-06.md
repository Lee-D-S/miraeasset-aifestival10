# 최근 3년 시계열 질문 수정 상태 (2026-09-06)

대상 질문: "회사의 최근 3년 &lt;지표&gt; 추이" (예: "삼성전자의 최근 3년 영업이익 추이를
알려줘"). 팀 내부 이름 T06은 원래 "매출액 추이" 한 건이다. 오늘 하루 이 질문군을
고치는 PR을 연달아 배포했다. **오늘이 코드프리즈**(`docs/ncp-deploy.md` 8절, 평가
09.07~09.30). 그럼에도 온종일 프리즈 안에서 배포를 이어가는 중이다.

## 실행 흐름 요약

```
interpreter → (계산 질문이면) calculation_planner → retriever(요구사항별 retrieve,
연도별 검색 분배) → reasoner(Fact 추출 → 화이트리스트 연산 → series) → validator
```

- `retriever/retrieval.py::retrieve()` — `filter_candidates`(SQL 후보) → 연도별
  키워드+벡터 검색(`_search_results_by_year`, `per_year ≈ branch_limit/연도수`) →
  병합·재랭킹 → `_diversify_by_year` → cited_documents.
- `retriever/local_store.py::_query_candidates()` — 운영 SQL 후보 생성.
- `reasoner/parsing/structured.py` + `reasoner/agents/fact_extraction.py` — 표 청크의
  `raw_json_content`(행별 JSON)를 격자로 삼아 수치 추출. `제NN기` 열 → `period_offset`
  → `base_year + period_offset`로 연도 변환. 증감/증감률 열은 건너뜀.

## origin/main 에 반영된 것 (커밋 3f27467, 프로덕션 배포됨)

| PR | 커밋 | 내용 |
|---|---|---|
| #37 | 8b52e75 | plan `percentage_change`에 연도별 series 부착 |
| #39 | bcd2db9 | 연도 3개 이상이면 series를 답변 "결론" 절에 표시 |
| #40 | 58b8362 | "당사의 매출" 회사 총계 문장 청크를 투자계획표보다 앞으로(검색 재랭킹). grounding에 계획표→not_total |
| #41 | 07cf8bf | 사실 선택 시 조 단위 서술을 백만원 표 셀보다 우선(`_amount_selection_rank`) |
| #43 | d53f675 | **C′**: 질의 salient 토큰 `text LIKE` 보조 후보 패스(`_salient_like_terms`). **A′**: `raw_json_content`를 SELECT·metadata로 통과 + `parse_structured_evidence(raw_json_content=)`. 영업이익 상한 `30_000_000`→`_OPERATING_PROFIT_MAX_MW=100_000_000`. 실기간 열 있으면 `증감/증감률` 열 스킵 |
| #44 | e4f9e1f | #40·#41의 매출 전용 규칙을 `metric=="revenue"`로 한정. `_pick_best*`가 후보에서 metric 읽음, `_record_from_fact`에 `metric` 포함 |
| #45/#46 | 5c9d902 | #44 병합 커밋(010cdc3)이 빠뜨린 `filter_candidates(query=query)` 재배선. 없으면 C′가 죽은 코드 |
| #46 | e4c461e | `AnswerWriter._template`가 "정보 한계" 절에서 retrieval_trace 줄(`query=`/`candidate_count=`/`reranker=`) 제거 |
| — | 15f329e | 포트 80 노출 (팀원) |
| #47 | 8e00fb8 | `CLOVA_CHAT_MODEL` 기본값 `HCX-DASH-002` → `HCX-005` (`integration/clova.py`, `docker-compose.yml` environment 블록, `.env.example`) |

## 배포 후 스모크 결과

- **매출 추이**: 3년 정상 (258→300→333조, 증감률 28.84%). 회귀 없음.
- **2024 영업이익 단일값**: 32,725,961백만원 (정확). 직전엔 "33조원" 반올림.
- **영업이익 3년 추이**: 여전히 2023 누락, 2025 "44조원". 미해결.
- `answer_mode`: 세 건 모두 `deterministic_grounding_fallback`. CLOVA 답이 필수
  주장 게이트를 통과 못 해 템플릿으로 대체(템플릿 내용 자체는 정확).

## 결정적 진단: 데이터는 깨끗하다, 검색 recall 문제다

프로덕션 인덱스 SQLite dump:

| 청크 | 섹션 | 영업이익 값 (백만원, 연결) |
|---|---|---|
| `20240312000736_2642` | IV. 이사의 경영진단 및 분석의견 | 제55기 6,566,976 / 제54기 43,376,630 (+증감·증감률 열) |
| `20240312000736_315` | 1. 요약재무정보 | 제55/54/53 = 6,566,976 / 43,376,630 / 51,633,856 + "제55기=2023년 1월~12월" 매핑 행 |
| `20260310002820_1552` | 3. 재무상태 및 영업실적 | 제57기 43,601,051 / 제56기 32,725,961 |
| `20260310002820_365` | 1. 요약재무정보 | 제57/56/55 = 43,601,051 / 32,725,961 / 6,566,976 + 연도 매핑 행 |

즉 정답 영업이익 3년치는 인덱스에 백만원 정밀도 JSON으로 이미 있다.
**재인덱싱 불필요.** `제NN기`→연도 변환도 코드에 이미 있다.

reasoner에 도달 못 한 이유:
- `_query_candidates`의 보조·베이스 패스가 모두 `ORDER BY id ASC LIMIT`. `id`가
  TEXT라 사전식 정렬이고, `_315`·`_2642`는 큰 사업보고서에서 `_1xxx`·`_10xxx`
  뒤로 정렬되어 후보 상한 밖으로 밀린다.
- 살아남아도 연도별 키워드 컷(`branch_limit/3 ≈ 33`)에서 떨어진다.
- 매출은 "당사의 매출은 X조 Y억원" 산문 + `_ensure_company_total_in_year`
  (revenue 전용)이 있어 이를 피한다. 영업이익은 둘 다 없다.

## 대기 중인 수정 — 브랜치 `fix/retrieve-financial-summary-sections`

푸시 완료. 커밋 `02577ba` + `aa864af`. `origin/main`(3f27467) 위로 rebase,
fast-forward 가능. PR 미생성(`gh`/토큰 없음).

1. `retriever/local_store.py::_query_candidates`
   - **패스 0 (보장)**: `WHERE <manifest> AND section_name LIKE '%요약재무%'
     ORDER BY id ASC LIMIT 40`, 병합 시 맨 앞. 요약재무정보는 보고서당 몇 개뿐이라
     작은 LIMIT로 100% 편입. 질의어가 텍스트에 없어도 됨.
   - 보조 term 패스: `ORDER BY (CASE WHEN section_name LIKE
     요약재무/경영진단/재무상태/영업실적 THEN 0 ELSE 1 END), id ASC` 추가
     (`section_name` 컬럼 있을 때만). `term_limit` `min(limit//2,600)` →
     `min(limit*3//4,900)`.
2. `retriever/retrieval.py::_search_results_by_year` — `_ensure_summary_table_in_year`:
   `operating_profit`·`net_income`에 한해 JSON 있는 요약재무정보/MD&A 섹션 청크를
   연도마다 1건 강제 편입. `revenue`는 불변.
3. `candidate_limit`/`branch_limit`는 전역 상향하지 않음. `ORDER BY id ASC`라
   ~10000까지 올려야 닿고, 벡터는 `_MAX_VECTOR_CANDIDATES=2000`에서 잘림. 전역 상향은
   전 질문 비용만 늘고 recall은 안 고침.
4. 테스트: `tests/test_local_store.py`(보장 패스, 섹션 우선순위),
   `tests/test_retriever.py`(`_ensure_summary_table_in_year` op-profit용, revenue 제외).
   `pytest` 288 passed / 2 기존 CORPUS_DIR 실패.

## 다음 절차

1. PR 생성: `https://github.com/miraeasset-aifestival-2026-dart/dis-164/pull/new/fix/retrieve-financial-summary-sections`
2. 머지 → 서버 `git pull` + `INDEX_DIR=/data/local_db docker compose up -d --build`
   → `/ready` 200.
3. 스모크 "삼성전자의 최근 3년 영업이익 추이를 알려줘". 기대값
   2023: 6,566,976 / 2024: 32,725,961 / 2025: 43,601,051 백만원, 증감률 ≈ 563.9%.
   "매출액 추이" 회귀 없음, "당기순이익 추이"도 같은 요약재무정보 청크로 동작 확인.

## 평가 이후 과제 (프리즈·스키마)

- FTS5(BM25) 인덱스 — SQL 후보 단계에서 진짜 어휘 관련도.
- chunk 숫자 순번 컬럼 — 문서 순서를 사전식 id 대신 실제 순서로.
- `answer_mode`가 계산 질문에서 항상 `deterministic_grounding_fallback` — HCX 답이
  `_has_required_claim` 게이트를 못 넘음. 게이트 조사.
- 연결 손익계산서 정식 섹션(`2-2. 연결 손익계산서`, `_339..347`)은 여전히 값 없이
  라벨만. 재파싱 필요. 현재는 요약재무정보/MD&A로 답변.

## curl (서버 스모크)

```bash
for q in \
  "삼성전자의 최근 3년 영업이익 추이를 알려줘" \
  "삼성전자의 최근 3년 당기순이익 추이를 알려줘" \
  "삼성전자의 최근 3년 매출액 추이를 알려줘" ; do
  echo "=== $q ==="
  curl -sG "http://127.0.0.1:8000/answer" \
    --data-urlencode "question_id=SMOKE" \
    --data-urlencode "question=$q" \
  | python3 -c '
import sys,json
r=json.load(sys.stdin); print(r["answer"])
tr=json.loads(r["think_trace"]).get("reasoner_result",{}).get("trace",[])
print("--- answer_mode:", [x for x in tr if "answer_mode" in x] or tr)'
  echo
done
```
