# 1단계 — 질의 이해 (코퍼스 인덱스 쿼리 빌더)

이 문서는 `stage1/` standalone 모듈 문서다. 전체 Stage123 실행은
workspace 루트의 `scripts/run_e2e.py`를 사용한다. JSON fixture E2E에서는
`integration/local_index.py`가 이 Stage1 계약에 맞는 local index를 구성한다.

공시 Agent의 1단계다. 자연어 질의를 **`manifest.jsonl`에 그대로 걸 수 있는 필터**로 바꾼다.

```
입력  : question (자연어 문자열 1개)
출력  : Intent JSON (manifest_filter + route)
안 함 : raw/ XML 열기, 수치 추출, RAG, 답변 작성
```

`answer` / `retrieved_context`는 3단계 산출물이다. 1단계는 "무엇을 찾을지"만 정한다.

## 실행

```bash
# 단건 (workspace 루트에서)
python -m stage1.main --corpus-dir <CORPUS_DIR> "삼성전자의 2025년 연결기준 매출액은?"

# 2단계에 넘길 필터만
python -m stage1.main --corpus-dir <CORPUS_DIR> --filter-only "현대건설이 2025년에 체결한 공급계약 정리해줘"

# think_trace용 한 줄 요약
python -m stage1.main --corpus-dir <CORPUS_DIR> --trace "2차전지 기업 중 2025년 설비투자가 가장 큰 곳은?"

# 골드셋 회귀
python -m stage1.main --corpus-dir <CORPUS_DIR> --gold

# 골드셋 + 불변식 검증 (표준 라이브러리만, pytest 불필요)
$env:CORPUS_DIR = "<CORPUS_DIR>"
python -m stage1.tests.run_checks

# pytest가 있으면
pytest stage1/tests
```

`<CORPUS_DIR>`에는 `universe.csv`와 `manifest.jsonl`이 모두 있어야 한다. `--gold`와
`stage1/tests/run_checks.py`는 이 실제 corpus의 기업·기간 경계를 사용하므로, corpus가 없으면
실행할 수 없다. `test_data/disclosure_clova_local.json`은 Stage1 standalone corpus가 아니라
Stage123 통합 E2E용 fixture다.

전체 통합 경로에서는 `integration/local_index.py`의 `LocalJsonCorpusIndex`가 JSON fixture의
기업·기간 정보를 Stage1 계약에 맞춰 구성한다. 따라서 통합 E2E를 실행할 때 Stage1 standalone
corpus를 별도로 준비하지 않는다.

단독 Stage1 실행과 Stage123 전체 실행은 다음처럼 구분한다.

| 목적 | 진입점 | 입력 |
|---|---|---|
| Stage1 standalone | `python -m stage1.main --corpus-dir <CORPUS_DIR> ...` | `universe.csv` + `manifest.jsonl` |
| Stage123 통합 E2E | `python scripts/run_e2e.py ...` | JSON fixture 또는 production adapter |

## 파이프라인

```
question
  ① preprocess       NFKC, 공백/물결/인용부호 정규화 (원문 보존)
  ② entity_linker    블로클리스트 → 기업 별칭(최장일치) → 섹터 → 모호토큰 → 접미어 휴리스틱
  ③ slot_extractor   연도·기간·공시유형·지표·intent·연결여부·정정여부
  ④ filter_builder   기본값 적용 + 코퍼스 경계 반영 → ManifestFilter
     router          unsafe > 범위밖(unanswerable) > 모호·정보부족(need_clarify) > ok
  ⑤ validator        enum 검증, manifest 후보 건수 확인, 최종 Intent
```

진입점은 하나다.

```python
from stage1 import CorpusIndex, build_intent

index = CorpusIndex.load()          # 프로세스 시작 시 1회
intent = build_intent(question, index)
docs = stage2.select(intent.manifest_filter)   # 2단계는 이것만 본다
```

## 출력 예시

```json
{
  "intent": "lookup",
  "route": "ok",
  "metric": "revenue",
  "basis": "연결",
  "manifest_filter": {
    "corp_names": ["삼성전자"],
    "doc_group": "periodic",
    "doc_subtype": "annual",
    "base_years": [2025],
    "base_months": [12],
    "is_correction": false
  },
  "doc_count": 1,
  "assumptions": ["연결/별도 기준이 명시되지 않아 연결기준으로 봅니다."]
}
```

## route

| 값 | 조건 | 다음 단계 |
|---|---|---|
| `ok` | 슬롯 충분, 코퍼스 범위 안 | 2단계 검색 |
| `need_clarify` | 기업 모호(`현대`, `삼성`) 또는 기업·섹터 둘 다 없음 | 역질문 |
| `unanswerable` | 2022년, 코퍼스 외 기업, 주가·뉴스, FY2026 사업보고서 | 검색 없이 한계 고지 |
| `unsafe` | 투자권유, 프롬프트 공격, 개인정보 | 거절 |

`route != ok`이면 `manifest_filter`를 비워 2단계가 실수로 조회하지 못하게 한다.

## 설계 결정 (왜 이렇게 했는지)

**조인 키는 항상 `corp_name`.** `raw/` 폴더명 = `manifest.corp_name`이다. 통용명은 표시용일 뿐이다.
현대차→현대자동차, KT→케이티, 엔씨소프트→NC, 삼성화재→삼성화재해상보험,
LS ELECTRIC→엘에스일렉트릭, LIG넥스원→LIG디펜스앤에어로스페이스, JYP Ent.→`JYP Ent`.

**매칭 순서가 정확도를 만든다.** 코퍼스 외 기업 블로클리스트를 먼저 소비해야
`카카오뱅크`가 `카카오`로 잘못 잡히지 않는다. 그다음 최장일치로 별칭을 소비해야
`삼성전자`가 `삼성`으로 쪼개지지 않는다.

**짧은 이름은 자동 매핑하지 않는다.** `현대`·`삼성`·`LG`는 후보가 여러 개라 `need_clarify`.
접두어 후보가 1개뿐이면(예: `신한`→신한지주) 자동 확정한다.

**섹터 질의는 기업을 임의로 고르지 않는다.** "2차전지 기업 A와 B"는 `sector`만 넘기고
멤버 3사를 `sector_members`로 준다. 1단계에서 2개를 찍으면 나머지 1개가 빠진다.

**시간은 두 축을 분리한다.** 정기공시는 보고기간(`base_year`/`base_month`),
수시공시는 접수일(`rcept_dt`). 섞으면 FY2025 사업보고서(2026-03 접수)가 탈락한다.
`base_*`는 정기공시 전용 필드라 수시공시에는 적용하지 않는다.

**코퍼스 경계는 두 종류다.** 정기공시는 FY2023~FY2025 전체 + **FY2026은 1분기(`base_month=3`)만**.
수시공시는 이벤트 기준 2023-01-01~2026-03-31(정정본 접수는 그 이후도 존재).
기간을 사용자가 **명시**했는데 코퍼스에 없으면 거절(FY2026 사업보고서),
기본값으로 채운 기간이 없으면 가능한 기간으로 폴백하고 `assumptions`에 남긴다.

**`major`는 `doc_subtype`이 없다.** manifest 598건 전부 `None`이라
유상증자·CB·EB 구분은 `report_nm_contains`로 한다. 자기주식은 자금조달이 아니라 별도 지표다.

**doc_group 후보가 둘이면 `doc_subtype`을 고정하지 않는다.** 설비투자처럼
`periodic`(사업보고서 본문)과 `exchange`(신규시설투자등)에 모두 있을 수 있는 지표는
후보 배열로 넘겨 2단계가 OR로 검색하게 한다.

**건수 0 ≠ 결측.** 정상 질의는 후보 문서가 0건이어도 `route=ok`로 넘기고 경고만 남긴다.
"공시에 없음" 판단은 2단계의 `not_found` 몫이다. 1단계에서 `unanswerable`을 남발하면
실제로 답할 수 있는 질의까지 죽는다. 신주인수권부사채(BW)는 이 코퍼스에 0건이다.

**평가는 1-shot이다.** 질의 1회 → 답변 1회이므로 역질문을 최소화한다
(`defaults.json`의 `clarify_policy.mode = minimal`). 기업이 모호하거나 아예 없을 때만
역질문하고, 기간·지표 결손은 기본값 + `assumptions`로 처리한다.

**LLM은 빈칸 채우기만.** 대회 규칙상 HyperCLOVA X만 쓸 수 있고, 기본은 비활성이다
(`STAGE1_USE_LLM=1`). 모델이 만든 기업명은 `universe`에 없으면 버리고, enum 밖 값도 버린다.
호출·파싱 실패 시 규칙 결과를 그대로 쓴다.

## 파일

```
config/
  aliases.json          별칭(구어체) + 모호 토큰. 값은 corp_name
  sector_aliases.json   섹터 표기 변형 (멤버는 universe에서 자동 생성)
  metric_router.json    지표 → doc_group / doc_subtype / report_nm_contains
  defaults.json         기본값, intent 단서, 상대시제, clarify 정책
  corpus_bounds.json    FY 가용 기간, 수시공시 접수 창
  guard_patterns.json   unsafe / out_of_scope / 코퍼스 외 기업
models/intent.py        Intent · ManifestFilter · CorpRef
index/corpus_index.py   universe·manifest 로드, 별칭·섹터 인덱스, count_docs
pipeline/               ①~⑤ 단계별 모듈, build_intent 진입점
llm/slot_filler.py      HyperCLOVA X 슬롯 보충 (기본 비활성)
tests/gold_queries.jsonl 골드 28건 (별칭·모호·섹터비교·범위밖·unsafe·0건)
```

## 2단계와의 계약

`ManifestFilter` 필드명과 enum은 `manifest.jsonl` 컬럼과 동일하게 유지한다.

| 필드 | manifest 대응 | 비고 |
|---|---|---|
| `corp_names` | `corp_name` | 비교 질의는 2개 이상 |
| `sector` | `sector` | `corp_names`가 비었을 때만 사용 |
| `doc_group` / `doc_group_candidates` | `doc_group` | 후보가 여럿이면 OR |
| `doc_subtype` / `doc_subtype_candidates` | `doc_subtype` | `major`는 항상 `None` |
| `base_years` / `base_months` | `base_year` / `base_month` | 정기공시 전용 |
| `rcept_from` / `rcept_to` | `rcept_dt` | 수시공시 |
| `is_correction` | `is_correction` | `None`이면 정정 무관 |
| `report_nm_contains` | `report_nm` | `major` 세부 구분 |

`allow_pdf_html`은 원본 XML이 없는 대체 수집 3건(`file_format=pdf+html`)을
2단계 파서가 분기 처리하라는 플래그다.

## 남은 작업 (2단계 이후)

- `doc_selector.select(manifest_filter)` → 문서 ID 목록
- 정정 체인 연결 (`correction_mode=include_chain`일 때 원본↔정정본 매칭)
- XML 파서 + 재무/계약 추출기, PDF·HTML 대체 경로
- 증감률·비교는 LLM이 아니라 코드로 계산
