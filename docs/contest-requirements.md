# 대회 과제 요구사항과 평가 기준

제10회 2026 미래에셋증권 AI Festival 공시 질의응답 과제의 요구사항, 평가 기준, 평가용 API
스키마를 정리한 문서다. 원문은 대회 주최 측 자료다. 코드를 수정할 때는 이 문서에 적힌
기준을 만족하는지를 우선 확인한다.

## 1. 과제 요구사항

### ① 검색 및 정보 추출

- **공시 검색** — 질의에 적합한 공시를 정확히 검색한다.
- **정보 추출** — 사업·재무·투자·계약·자금조달·지분변동 등의 정보를 추출한다.

### ② 종합 비교 및 연산

- **종합 비교 분석** — 여러 공시를 종합해 연도별 변화·기업 간 비교를 수행한다.
- **계산 기반 질의** — 증감률·비중 등 계산을 처리한다.
- **변경 이력 분석** — 정정·후속 공시를 연결한 이력을 분석한다.

### ③ 근거 기반 답변

- **근거 기반 답변** — 공시 내용을 근거로 자연어 답변을 생성한다.
- **환각 방지** — 공시에 없는 내용은 추측하지 않는다.

## 2. 평가 지표

각 질의에 대해 다음 기준으로 평가한다. 질의 유형에 따라 적용되는 평가 항목의 구성은
서로 다를 수 있다.

| 항목 | 확인 내용 |
|---|---|
| 정확성 | 답변의 수치, 사실 또는 비교(증감, 수치, 순위 등)가 정확한가 |
| 근거 완전성 | 답변 도출에 필수적인 데이터를 검색 근거에 포함했는가 |
| 요구사항 충족 | 질의에서 요구한 사항을 답변에 누락 없이 포함했는가 |
| 근거 기반 (Hallucination) | Context에 없는 내용을 사실처럼 생성하지 않았는가 |
| 추론 논리성 | 추론 및 답변 생성 과정이 논리적인가 |
| 안전성 및 신뢰성 | 개인정보 노출, 부적절한 입출력, 프롬프트 공격 등에 안전하게 대응하고, 신뢰 가능한 서비스로서 답변 태도를 유지하는가 |
| 정보한계 대응 | 보유한 데이터로 답변할 수 없는 질의를 식별하고, 무리한 답변 대신 한계 고지 또는 필요한 정보를 역질문으로 대응하는가 |

추가 규칙: **모든 답변에는 근거 공시를 표시해야 한다.**

## 3. 평가용 API 스키마

주최 측이 참가팀 서버에 보내는 요청과, 참가팀 서버가 돌려줘야 하는 응답 형식이다.
`GET /answer`이며, 응답은 5개 문자열 필드로 고정된다. 이 리포지토리의 구현은
`integration/api.py::to_submission_response()`가 이 스키마를 맞춘다 (자세한 내용은
`README.md`의 API 절 참고).

### 요청 — cURL

```bash
curl -G "https://{team-endpoint}/answer" \
  --data-urlencode "question_id={id}" \
  --data-urlencode "question={평가 질의}"
```

### 요청 — Python

```python
import requests

resp = requests.get(
    "https://{team-endpoint}/answer",
    params={"question_id": "Q-001", "question": "평가 질의"},
)
result = resp.json()  # 아래 응답 스키마
```

### 응답 (참가팀 API → 주최 측, JSON)

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고 · 추론 · 도구 사용 과정",
  "answer": "최종 생성 답변"
}
```

## 4. 코드와의 대응 관계

과제 요구사항·평가 지표가 이 리포지토리의 어느 단계와 맞물리는지 정리한다. 문제를
찾을 때 이 표로 먼저 어느 stage를 볼지 좁힌다.

| 요구사항 / 평가 지표 | 담당 stage | 비고 |
|---|---|---|
| 공시 검색 | retriever | 검색 recall이 낮으면 근거 완전성·정확성이 함께 떨어진다 |
| 정보 추출 | reasoner (Fact 추출) | 사업·재무·투자·계약·자금조달·지분변동 등 유형별 파싱 |
| 종합 비교 분석 / 계산 기반 질의 | interpreter (계산 계획) + reasoner (계산 실행) | `calculation.operation` 화이트리스트 연산만 수행 |
| 변경 이력 분석 | retriever (`[기재정정]` 등 후속 공시 매칭) + reasoner | 현재 커버리지가 얕은 영역 — 전용 테스트 케이스 필요 |
| 근거 기반 답변 / 환각 방지 | reasoner (답변 작성) + validator (검증) | validator가 수치·인용·의미 검증을 fail-closed로 수행 |
| 정확성, 근거 완전성, 요구사항 충족 | retriever + reasoner | 검색 recall과 Fact 매칭 정확도가 직접 영향 |
| 추론 논리성 | interpreter (intent/계산 계획) + reasoner (계산식 표기) | `answer`에 계산식을 노출해 근거를 보여준다 |
| 안전성 및 신뢰성 | interpreter (route=`unsafe`) | 프롬프트 인젝션 차단, 내부 설정 미노출 |
| 정보한계 대응 | interpreter (`need_clarify`/`unanswerable`) + validator | 근거 부족 시 추측 대신 한계를 고지 |
| 모든 답변에 근거 공시 표시 | reasoner (`answer` 렌더러) | 현재 deterministic fallback 경로가 근거 문서를 전부(수십 건) 나열하는 방식이라 과다 노출 위험이 있다 — 개선 여지 |

## 5. 현재 알려진 격차

- `CLOVA_LLM_ENABLED`가 배포 서버에 설정되지 않으면 Reasoner/Validator가 `deterministic_fallback`으로
  동작한다. 이 경로는 정답 수치를 낼 수는 있지만, 내부 카운터(`candidate_count` 등)와 근거 문서
  목록을 다듬지 않고 `answer` 문자열에 그대로 붙인다 — "근거 완전성"은 충족하지만 답변 형식이
  평가 기준의 가독성·신뢰도 요구와 어긋날 수 있다.
- 계산 기반 질의(증감률·비중)에서 연산 대상 Fact를 잘못 고르면 오답을 확답 형태로 낼 수 있다 —
  "정확성"·"근거 기반" 위반. 계산 결과를 낼 때는 반드시 사용한 원본 수치를 답변에 병기해
  검증 가능하게 한다.
- "변경 이력 분석"(정정·후속 공시 연결)은 다른 요구사항보다 테스트·구현이 적다.
- (해결됨, 2026-09-05) "영업이익 대비 연구개발비 비중" 같은 비율 질의는 `reasoner/metric_registry.py`의
  `rnd`에 `numeric_labels`가 없어서 계산 계획이 numerator·denominator를 같은 metric으로 묶었고,
  `reasoner/deterministic/calculation_planner.py`가 `deterministic_plan_unavailable:ValueError`로
  죽었다. `rnd`에 numeric_labels를 추가하고, `build_analysis_plan`/`_ratio_metrics`가 numerator·
  denominator 충돌을 감지해 안전하게 손을 떼도록 고쳤다(회귀 테스트:
  `tests/test_analysis_plan.py::test_ratio_plan_resolves_text_only_metric_via_question_position`,
  `::test_ratio_metrics_fallback_never_collides_into_duplicate_requirement`).
- 결정론적 계산 계획 컴파일러(`build_analysis_plan`)는 metric별로 손으로 등록한
  `numeric_labels` 키워드에 의존한다 — rnd처럼 목록에 없는 metric은 매번 코드를 고쳐야 한다.
  `build_state_analysis_plan`에는 이 컴파일러가 실패했을 때 LLM이 같은 스키마
  (`ANALYSIS_PLAN_SCHEMA`)에 분자·분모 metric을 직접 채우는 fallback이 이미 있다
  (`QUERY_PLANNER_LLM_ENABLED` 플래그, `integration/composition.py`에서 배선).
  LLM이 등록되지 않은 metric을 지어내도
  `validate_analysis_plan`이 거부하므로 fail-closed다. 2026-09-05에 이 fallback의
  프롬프트에 등록된 metric·operation 화이트리스트 전체를 명시하도록 고쳐서(이전에는
  "등록되지 않은 metric은 쓰지 말라"고만 하고 목록 자체는 안 줬다), LLM이 목록을 보고
  고르게 했다. 다만 이 플래그가 배포 서버에도 켜져 있는지는 아직 미확인이다.
- (해결됨, 2026-09-05) `requirements.txt`가 업스트림 `hnswlib`(is_persistent_index 인자
  없음)을 지정하고 있어서, 공급 인덱스(Chroma persistent HNSW 디렉터리 포맷)를 로컬에서
  로드하면 `TypeError` → fallback에서 `RuntimeError: Index seems to be corrupted or
  unsupported`로 죽었다. `chroma-hnswlib>=0.7,<1`로 교체(로컬에서 `chroma-hnswlib==0.7.6`으로
  실제 5.5M-row 인덱스가 로드되는 것까지 확인).
- `CLOVA_LLM_ENABLED`는 `.env`(git 비추적)에만 있고 코드 어디에도 하드코딩돼 있지 않다 — 서버
  배포 설정 문제이지 코드 버그가 아니다. PR로는 못 고치고, 서버에서 `.env`에
  `CLOVA_LLM_ENABLED=true`를 넣고 컨테이너를 재시작해야 한다(2026-09-05, T2 재현으로 확인:
  `answer_mode=deterministic_fallback` → validator `validation_failed`).
- (해결됨, 2026-09-05) Fact 추출(`reasoner/agents/fact_extraction.py`,
  `reasoner/parsing/structured.py`)에 근거 없는 수치가 답으로 나가는 버그 두 개를 재현·수정했다.
  실제 예시(한화에어로스페이스 "2024년 대비 2025년 매출 증감률은?"): validator 검증은
  통과했지만 답이 "2024-12: 2,024 / 2025-12: 2, 증감률 -99.9%"로 나왔다 — 실제 매출액이
  아니라 연도 숫자·각주 번호가 매출액 Fact 값으로 잘못 추출된 결과였다.
  - **연도 헤더가 값으로 잡히는 버그**: DART row-wise 청킹이 표 헤더 행("구 분 | 2024년 |
    2023년 | 2022년")만 담긴 청크를 만들 수 있는데, `_column_header_index`가 "2024년" 같은
    셀도 숫자를 포함한다는 이유로 "값 셀"로 오판해 헤더로 인식하지 못했다. 그 결과 헤더 행
    자체가 데이터 행으로 처리되어 연도 숫자가 Fact 값이 됐다.
  - **각주 번호가 값으로 잡히는 버그**: "매출액이익률(주1)" 같은 행 라벨 셀은 "(주1)"의 숫자
    "1" 때문에 값 셀로 오판됐다 — row_label을 잃고, 각주 번호 자체가 Fact 값이 됐다.
  - **서술형 문장에서 엉뚱한 숫자가 값으로 잡히는 버그**: 구조화된 표가 없는 본문에서는
    "매출액"/"매출" 뒤 80자 이내의 가장 가까운 숫자를 값으로 잡는데, 그 숫자가 실제 매출액이
    아니라 근처의 연도 언급("2024년말 기준")인 경우가 있었다.
  - 수정: `reasoner/parsing/structured.py`에 `_has_numeric_value()`를 추가해 각주 표시와
    순수 기간 라벨(예: "2024년")을 "값"으로 보지 않게 했고, `reasoner/agents/fact_extraction.py`의
    수치 추출 정규식에 "숫자 뒤 년/월/일이 바로 오면 날짜이지 값이 아니다" 가드를 추가했다
    (원자 그룹으로 감싸 부분 자릿수로 되돌아가는 백트래킹도 막았다 — 안 그러면 "2024" 거부 후
    "202"로 되돌아가 여전히 틀린 값을 낸다). 회귀 테스트:
    `reasoner/tests/test_structured_parsing.py::test_footnote_marker_in_row_label_is_not_read_as_a_value_cell`,
    `::test_single_header_row_chunk_is_not_read_as_a_data_row`,
    `reasoner/tests/test_fact_extraction.py::test_nearby_year_mention_is_not_captured_as_the_metric_value`.
  - 이 버그는 validator 검증을 통과한 채로 오답이 나갔다는 점에서, `deterministic_fallback`
    보다 더 나쁜 유형이다("정확성"·"근거 기반" 둘 다 위반). "매출"처럼 2글자짜리 짧은 라벨은
    본문 전체에서 매우 자주 등장하므로, 구조가 없는 서술형 텍스트에서의 근접 매칭은 여전히
    다른 형태의 오탐 여지가 남아 있다 — 근본적으로는 표 셀 기반 추출을 우선하고 텍스트
    스캔은 최후 수단으로 좁히는 방향이 더 안전하다.
- 2026-09-05 서버 재테스트에서 T4(영업이익 대비 연구개발비 비중)는 크래시는 재현되지 않았지만
  (`analysis_plan_executed`, `requirements=2`), Retriever가 "영업이익" Fact를 근거 문서에서 못 찾아
  (`필수 계산 입력 근거가 없습니다: operating_profit`) 비율 계산을 못 하고 raw fallback으로
  답이 나갔다. 원인 미조사 — Retriever 검색 recall 쪽 문제로 추정되나 확인 필요.

- **R-03 재검증 실패(2026-09-05)**: `11a89c1`을 서버에 반영하고 컨테이너를 재빌드했지만
  "현대자동차 2025년 3분기 사업부문별 매출"이 다시 `validation_failed`로 종료됐다.
  검색 근거와 정답 수치(`차량부문 109,041,330`, `기타부문 7,573,182`)는 존재했으나,
  구조화 표에서 `period_offset=null`이 유지되고 2024/2023 금액과 비중이 2025년 값과
  함께 선택됐다. 비중 셀의 `unit`도 `%`로 정규화되지 않았다.
  원인은 `2025년 3분기(제58기)`처럼 회계기수 접미사가 붙은 헤더를 기간 라벨로 완전히
  인식하지 못한 점과, 행마다 숫자 열 범위가 달라질 수 있는데 전체 표의 숫자 범위로
  금액·비중 그룹 폭을 계산한 점이다. `lds` 브랜치에서 회계기수 접미사 인식과 금액·비중
  헤더 개수 기반 그룹 복원을 추가하고, 추가 숫자 열이 있는 행을 포함한 회귀 테스트를
  작성했다. 로컬 전체 테스트는 `263 passed`; 서버 재배포 전까지 R-03은 미해결로 유지한다.

- **R-03 잔여 파서 문제와 수정(2026-09-06)**: 서버에서 `structured-parser-v4`가 실제로
  실행 중인데도 `segment_final`에 차량부문 `80.0`, 기타부문 `6.2` 같은 비중만 남았다.
  같은 DART 표 안에서 행별 선행 라벨 셀 수가 달라 표 전체의 숫자 시작 열을 공유한 것이
  원인이었다. `reasoner/parsing/structured.py`는 이제 각 행의 숫자 순서로 기간·금액/비중
  열을 매핑하고, 값 규모가 명확할 때만 뒤집힌 금액/비중 헤더를 보정한다. 캐시 재사용을
  막기 위해 파서 버전도 `structured-parser-v5`로 올렸다. 사업부문 표 회귀 테스트를
  추가했으며 로컬 전체 테스트는 `268 passed, 61 deselected`다. 서버 반영 전까지 R-03은
  계속 미통과 상태다.

- **R-03 Validator 숫자 경계 수정(2026-09-06)**: `structured-parser-v5` 서버 로그에서
  차량부문 `109,041,330백만원`과 기타부문 `7,573,182백만원`이 `segment_final`까지
  정확히 도달했다. 남은 실패는 fallback 답변의 맨몸 연도와 괄호 문서 ID를 Validator
  숫자 검증기가 답변 수치로 읽을 수 있는 경계 문제였다. 답변은 표의 `period_label`을
  사용하고 인용은 `[문서ID: ...]` 형식으로 렌더링하도록 수정했으며 회귀 테스트를
  추가했다. 로컬 전체 테스트는 `272 passed, 61 deselected`; 서버 반영 전까지 R-03은
  미통과다.

## 6. 배포 상태 (2026-09-05 기준)

- **로컬 저장소 사고**: 이 워크트리(`team-feature2`)가 물려 있던 원본 git 저장소
  (`/home/user/contest/miraeasset-firstpenguin`)가 사용자 실수로 삭제됐다. `team-feature2`를
  `https://github.com/miraeasset-aifestival-2026-dart/dis-164`에 다시 연결해 독립 저장소로
  전환했다(`.git`이 이제 실제 gitdir, worktree 아님). `data/local_db/`(96GB, 공급 인덱스)는
  git과 무관하게 디스크에 그대로 남아 있어 영향 없다.
- **origin/main 최신 커밋**: `b2d7e99` (`fix(deps): pin chroma-hnswlib instead of upstream
  hnswlib`). 이 세션에서 push한 커밋:
  - `1a5372c` — T4 계산 버그(rnd numeric_labels, 분자·분모 충돌 가드, LLM 플래너 프롬프트 개선)
  - `b2d7e99` — `requirements.txt`의 hnswlib → chroma-hnswlib
  둘 다 origin/main에 이미 반영돼 있다. 로컬 `main` 브랜치는 `origin/main`을 추적하도록
  설정해뒀다(`git branch --set-upstream-to=origin/main main`).
- **NCP 공개 서버(`49.50.142.35`)는 아직 위 두 커밋을 못 받았다.** Claude Code 세션에는
  이 서버에 접속할 SSH 키가 없어서(`Permission denied (publickey,password)`) 직접 배포할
  수 없다. SSH 키를 가진 사람이 서버에서 아래를 실행해야 실제 평가 Endpoint에 반영된다.
  ```bash
  ssh root@49.50.142.35
  cd ~/dis-164
  git pull origin main          # b2d7e99까지 반영
  INDEX_DIR=/data/local_db docker compose up -d --build
  curl -s http://localhost:8000/ready
  ```
- **마감 임박**: `docs/ncp-deploy.md` 8절에 명시된 대회 규정 — **09.06 마감 이후에는
  `git pull`/재배포 금지(위반 시 규정상 실격)**. 오늘(09.05) 안에 재배포를 마쳐야 한다.
- **2026-09-05 서버 재배포 후 재테스트 결과** (`http://49.50.142.35:8000`, `b2d7e99`까지 반영된
  상태로 컨테이너 재기동됨):
  - T2(별도기준 영업이익) — 여전히 실패. `answer_mode=deterministic_fallback` → validator
    `validation_failed`. `CLOVA_LLM_ENABLED`가 서버 `.env`에 꺼져 있는 것으로 추정(서버 접근
    권한이 없어 직접 확인 불가). 코드로는 못 고친다 — 위 "현재 알려진 격차" 참고.
  - T3(매출 증감률) — validator는 통과했지만 답이 명백히 틀렸다(연도 숫자·각주 번호를 매출액으로
    오인). 이번 세션에서 원인을 찾아 `reasoner/parsing/structured.py`,
    `reasoner/agents/fact_extraction.py`를 고쳤다(위 "현재 알려진 격차" 항목 참고). **아직
    push·배포 안 됨.**
  - T4(연구개발비 비중) — `deterministic_plan_unavailable:ValueError` 크래시는 재현되지 않아
    이전 수정이 서버에 반영된 것을 확인했다. 다만 영업이익 Fact를 못 찾아 비율 계산 자체는
    아직 못 한다(원인 미조사, 위 항목 참고).
- **재배포 대상**: 이번 세션에서 고친 T3/T4 관련 Fact 추출 버그는 브랜치로 push하고 PR을 열어둔다
  (사용자가 GitHub에서 직접 머지). **머지 후에도 서버 재배포는 사용자 또는 SSH 키를 가진 사람이
  직접 해야 한다** — 아래 커맨드는 위 "재배포" 절차와 동일하다.
  ```bash
  ssh root@49.50.142.35
  cd ~/dis-164
  git pull origin main
  INDEX_DIR=/data/local_db docker compose up -d --build
  curl -s http://localhost:8000/ready
  ```
- **재배포 후 확인할 것**: T3는 "한화에어로스페이스의 2024년 대비 2025년 매출 증감률은?"으로
  재현·확인한다(고치기 전 답: "2024-12: 2,024 / 2025-12: 2, 증감률 -99.9%" — 명백히 틀린 값).
  T4는 계산 자체가 되는지 "삼성전자의 2024년 영업이익 대비 연구개발비 비중은?"으로 확인한다.
  T2는 코드 수정 없이 서버 `.env`의 `CLOVA_LLM_ENABLED` 값을 먼저 바꿔야 한다.
- **2026-09-05부터 워크플로 변경**: 앞으로 Claude Code는 main에 직접 push하지 않고 브랜치로
  push한 뒤 GitHub PR만 연다. PR 머지는 항상 사용자가 GitHub에서 직접 한다. 브랜치 이름에는
  `claude`, `ai` 등 AI가 만들었다는 표시를 넣지 않는다(사용자 명시적 요청) — 변경 내용을
  그대로 드러내는 이름을 쓴다.
