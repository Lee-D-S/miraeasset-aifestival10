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
| 공시 검색 | stage2 | 검색 recall이 낮으면 근거 완전성·정확성이 함께 떨어진다 |
| 정보 추출 | stage3 (Fact 추출) | 사업·재무·투자·계약·자금조달·지분변동 등 유형별 파싱 |
| 종합 비교 분석 / 계산 기반 질의 | stage1 (계산 계획) + stage3 (계산 실행) | `calculation.operation` 화이트리스트 연산만 수행 |
| 변경 이력 분석 | stage2 (`[기재정정]` 등 후속 공시 매칭) + stage3 | 현재 커버리지가 얕은 영역 — 전용 테스트 케이스 필요 |
| 근거 기반 답변 / 환각 방지 | stage3 (답변 작성) + stage4 (검증) | stage4가 수치·인용·의미 검증을 fail-closed로 수행 |
| 정확성, 근거 완전성, 요구사항 충족 | stage2 + stage3 | 검색 recall과 Fact 매칭 정확도가 직접 영향 |
| 추론 논리성 | stage1 (intent/계산 계획) + stage3 (계산식 표기) | `answer`에 계산식을 노출해 근거를 보여준다 |
| 안전성 및 신뢰성 | stage1 (route=`unsafe`) | 프롬프트 인젝션 차단, 내부 설정 미노출 |
| 정보한계 대응 | stage1 (`need_clarify`/`unanswerable`) + stage4 | 근거 부족 시 추측 대신 한계를 고지 |
| 모든 답변에 근거 공시 표시 | stage3 (`answer` 렌더러) | 현재 deterministic fallback 경로가 근거 문서를 전부(수십 건) 나열하는 방식이라 과다 노출 위험이 있다 — 개선 여지 |

## 5. 현재 알려진 격차

- `CLOVA_LLM_ENABLED`가 배포 서버에 설정되지 않으면 Stage3/Stage4가 `deterministic_fallback`으로
  동작한다. 이 경로는 정답 수치를 낼 수는 있지만, 내부 카운터(`candidate_count` 등)와 근거 문서
  목록을 다듬지 않고 `answer` 문자열에 그대로 붙인다 — "근거 완전성"은 충족하지만 답변 형식이
  평가 기준의 가독성·신뢰도 요구와 어긋날 수 있다.
- 계산 기반 질의(증감률·비중)에서 연산 대상 Fact를 잘못 고르면 오답을 확답 형태로 낼 수 있다 —
  "정확성"·"근거 기반" 위반. 계산 결과를 낼 때는 반드시 사용한 원본 수치를 답변에 병기해
  검증 가능하게 한다.
- "변경 이력 분석"(정정·후속 공시 연결)은 다른 요구사항보다 테스트·구현이 적다.
- (해결됨, 2026-09-05) "영업이익 대비 연구개발비 비중" 같은 비율 질의는 `stage3/metric_registry.py`의
  `rnd`에 `numeric_labels`가 없어서 계산 계획이 numerator·denominator를 같은 metric으로 묶었고,
  `stage3/deterministic/calculation_planner.py`가 `deterministic_plan_unavailable:ValueError`로
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
  `git pull`/재배포 금지(위반 시 규정상 실격)**. 오늘(09.05) 안에 위 재배포를 마쳐야 한다.
- **재배포 후 확인할 것**: T2(별도기준 조회, `stage4`가 `validation_failed`로 죽던 문제)는
  `CLOVA_LLM_ENABLED`가 서버 `.env`에 설정돼 있는지에 달려 있다(2026-09-04 진단, 아직
  서버에서 미확인). T4는 이번 커밋으로 코드 자체는 고쳤으니 재배포 후 재현 질의로 확인한다:
  `삼성전자의 2024년 영업이익 대비 연구개발비 비중은?`
