# HyperCLOVA X 호출 조건 및 테스트 질문

이 문서는 현재 `C:\projects\dis-164`의 canonical `build_pipeline()` 기준으로,
어떤 조건에서 CLOVA API가 호출되는지 확인하고, 호출 경로별 테스트 질문과
`GET /answer` 쿼리를 정리한 문서다.

## 1. 먼저 알아둘 점

질문이 모호하다는 사실만으로 CLOVA X가 항상 호출되는 것은 아니다. 다음 조건이
함께 맞아야 한다.

- 해당 기능의 환경 플래그가 켜져 있어야 한다.
- 요청이 그 단계까지 정상적으로 도달해야 한다.
- 해당 단계에서 규칙 기반 결과가 없거나, 외부 모델을 사용하도록 구성되어 있어야 한다.
- CLOVA 호출이 실패해도 대부분의 단계는 결정적 fallback으로 계속 진행한다. 따라서
  최종 답변이 나왔다고 해서 CLOVA 호출 성공을 의미하지는 않는다.

테스트할 때는 호출을 구분할 수 있도록 한 번에 하나의 기능 플래그만 켜는 것을 권장한다.
`CLOVA_LLM_ENABLED=1`은 답변 생성과 semantic 검증에 같은 Chat client를 사용하므로,
정상적인 한 질문에서 두 번의 Chat API 호출이 발생할 수 있다.

## 2. 현재 코드의 CLOVA 호출 지점

| ID | 호출 지점 | API operation | 호출 조건 | canonical `/answer`에서 질문으로 유도 가능 여부 |
| --- | --- | --- | --- | --- |
| C1 | Interpreter 슬롯 보충 | `semantic_validation` 기본값 | `INTERPRETER_USE_LLM=1`이고 기업·섹터·지표·의도 중 규칙으로 채우지 못한 슬롯이 있음 | 가능. 단, 호출 여부는 API 응답의 public trace에 직접 표시되지 않음 |
| C2 | Calculation Planner LLM fallback | `calculation_planning` | `QUERY_PLANNER_LLM_ENABLED=1`, route가 `ok`, Supervisor가 planner를 선택하고 결정적 planner가 `None`을 반환 | 가능. 단, 질문 표현을 의도적으로 결정적 planner가 해석하지 못하게 해야 함 |
| C3 | Reasoner 답변 생성 | `answer_generation` | `CLOVA_LLM_ENABLED=1`이고 근거를 사용한 Reasoner 결과가 성공 또는 부분 성공 | 가능. `reasoner.trace`의 `answer_mode=hyperclova_x`로 확인 |
| C4 | Validator semantic 검증 | `semantic_validation` | `CLOVA_LLM_ENABLED=1`이고 Validator가 semantic client를 보유한 상태로 실행됨 | 가능. C3와 같은 요청에서 연속 호출될 수 있음 |
| C5 | Retriever reranking | `reranker` | `CLOVA_RERANKER_ENABLED=1`, route가 `ok`, 키워드·벡터 병합 결과에 문서가 있고 각 문서에 ID·본문이 있음 | 가능. `retriever.trace`의 `reranker=clova`로 확인 |
| C6 | 답변 재생성 | `answer_generation` | Validator 결과가 `validation_failed`이고 재생성 횟수가 1회 미만 | 질문만으로 보장 불가. C4의 실패가 선행되어야 함 |
| C7 | Structured Supervisor | 별도 `model.invoke` | `StructuredSupervisorClient`를 Supervisor에 주입한 경우 | 현재 canonical `build_pipeline()`에서는 주입하지 않으므로 해당 질문 없음 |

참고로 `retriever/experiment_service.py`의 `EXPERIMENT_LIVE_LLM` 경로는 실험용
pipeline이다. 아래 테스트는 대회 제출용 canonical pipeline 기준으로 작성한다.

## 3. 호출 경로별 테스트 환경

각 케이스는 서버를 해당 환경으로 재기동한 뒤 단독 실행한다. API 키 값은 문서나
명령 이력에 직접 넣지 않고 실행 환경의 secret으로 주입한다.

### C1. Interpreter 슬롯 보충

```text
INTERPRETER_USE_LLM=1
QUERY_PLANNER_LLM_ENABLED=0
CLOVA_LLM_ENABLED=0
CLOVA_RERANKER_ENABLED=0
```

### C2. Calculation Planner LLM fallback

```text
INTERPRETER_USE_LLM=0
QUERY_PLANNER_LLM_ENABLED=1
CLOVA_LLM_ENABLED=0
CLOVA_RERANKER_ENABLED=0
```

### C3 + C4. 답변 생성 및 semantic 검증

```text
INTERPRETER_USE_LLM=0
QUERY_PLANNER_LLM_ENABLED=0
CLOVA_LLM_ENABLED=1
CLOVA_RERANKER_ENABLED=0
```

정상적인 근거 기반 질문이라면 일반적으로 `answer_generation` 후
`semantic_validation` 순서로 호출을 확인한다.

### C5. CLOVA reranker

```text
INTERPRETER_USE_LLM=0
QUERY_PLANNER_LLM_ENABLED=0
CLOVA_LLM_ENABLED=0
CLOVA_RERANKER_ENABLED=1
```

## 4. 테스트 질문과 쿼리

아래의 `$BASE_URL`은 실제 실행 주소로 바꾼다. `--data-urlencode`를 사용하므로
한글 질문을 직접 URL 인코딩할 필요가 없다.

### C1-01. 지표가 모호한 질문 — 슬롯 보충

질문:

> 삼성전자 2025년 실적이 어떻게 됐어?

규칙으로 기업과 연도는 찾을 수 있지만 `실적`만으로는 등록 지표를 확정할 수 없다.
따라서 `slots.metric is None` 조건으로 슬롯 보충 호출을 유도한다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C1-01" `
  --data-urlencode "question=삼성전자 2025년 실적이 어떻게 됐어?"
```

확인 기준:

- 직접 `build_intent()`를 호출하는 테스트에서는 `intent.llm_used == True`인지 확인한다.
- `/answer` public response에서는 현재 `llm_used`가 `think_trace`에 노출되지 않는다. 따라서
  이 케이스는 CLOVA Studio 요청 로그 또는 테스트 double의 `generate_json` 호출 횟수로 확인해야 한다.
- 모델이 `revenue`, `operating_profit` 등 허용된 등록 지표를 채우지 못하면 호출은 되었어도
  규칙 결과가 유지될 수 있다.

### C1-02. 기업이 생략된 질문 — 기업 슬롯 보충

질문:

> 2025년 연결기준 매출액은 얼마인가?

기업·섹터가 모두 비어 있으므로 `_should_call_llm()`의 기업 슬롯 조건을 직접 자극한다.
모델이 기업을 채우지 못하면 이후 단계는 `need_clarify`로 종료될 수 있지만, 이 경우에도
슬롯 보충 호출 자체를 확인하는 테스트로는 유효하다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C1-02" `
  --data-urlencode "question=2025년 연결기준 매출액은 얼마인가?"
```

### C2-01. 계산 의미가 모호하고 결정적 연산에 없는 질문 — Planner fallback

질문:

> 삼성전자의 2024년과 2025년 매출액의 누적 변동폭을 계산해줘.

질문은 계산 의도로 분류되고 기업·지표·두 연도도 갖지만, 현재 결정적 연산 사전에는
`누적 변동폭`이 없다. 따라서 결정적 `build_analysis_plan()`이 계획을 만들지 못하면
`calculation_planning` operation으로 HyperCLOVA X fallback을 호출한다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C2-01" `
  --data-urlencode "question=삼성전자의 2024년과 2025년 매출액의 누적 변동폭을 계산해줘."
```

확인 기준:

- `think_trace.analysis_plan.trace`에 `planner=llm`이 있어야 한다.
- `planner=deterministic`이면 이 질문이 현재 Interpreter에서 이미 지원 연산으로 해석된
  것이므로 C2 테스트는 실패로 기록하고 질문을 조정한다.
- `planner=llm_plan_invalid` 또는 `planner_failed`도 호출 자체는 발생한 것이므로,
  호출 여부와 계획 품질은 별도로 기록한다.

### C3-01. 근거가 있는 수치 질문 — 답변 생성

질문:

> 삼성전자의 2024년 연결기준 영업이익은 얼마인가?

기업·연도·지표가 명확하고, 제공 코퍼스의 대표적인 수치 조회 형태이므로 Retriever가
근거를 찾으면 Reasoner가 HyperCLOVA X로 답변 초안을 생성한다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C3-01" `
  --data-urlencode "question=삼성전자의 2024년 연결기준 영업이익은 얼마인가?"
```

확인 기준:

- `think_trace.reasoner_result.trace`에 `answer_mode=hyperclova_x`가 있어야 한다.
- provider 오류가 나면 `answer_mode=deterministic_fallback`으로 남을 수 있다. 이 경우에도
  `provider_status`와 서버 로그를 함께 확인한다.

### C4-01. 동일 수치 질문 — semantic 검증

C4는 C3와 같은 `CLOVA_LLM_ENABLED=1` 설정에서 실행한다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C4-01" `
  --data-urlencode "question=삼성전자의 2024년 연결기준 영업이익은 얼마인가?"
```

확인 기준:

- `think_trace.validator_result.trace`에 `validated`가 있어야 한다.
- `semantic_provider_deterministic_fallback`, `provider_rate_limited_deterministic_grounding`,
  `validator_error`가 있으면 실제 semantic API 결과가 아니라 fallback일 수 있다.
- 정상 호출 여부를 확정하려면 CLOVA 요청 로그에서 `operation=semantic_validation`을 확인한다.

### C5-01. 검색 결과가 있는 질문 — CLOVA reranker

질문:

> 삼성전자의 2025년 연결기준 매출액은 얼마인가?

기업·기간·매출 지표가 명확해 후보 문서가 생기기 쉬운 질문이다. C5 환경에서는 답변용
Chat client가 꺼져 있으므로 reranker 호출만 분리해서 확인할 수 있다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C5-01" `
  --data-urlencode "question=삼성전자의 2025년 연결기준 매출액은 얼마인가?"
```

확인 기준:

- `think_trace.retriever_result.trace`에 `reranker=clova`가 있어야 한다.
- `retriever_result.provider_status.operation`이 `reranker`인지 확인한다.
- `candidate_count`, `keyword_count`, `vector_count`가 모두 0이면 문서가 없어 CLOVA reranker에
  보낼 payload가 없으므로 질문 자체가 C5 조건을 만족하지 못한 것이다.

### C6-01. Validator 실패 후 답변 재생성 — 조건부 테스트

질문 후보:

> 현대자동차 2025년 3분기 사업부문별 매출을 알려줘.

이 질문은 사업부문별·분기별 범위형 수치가 여러 개라 답변의 숫자와 citation을 엄격하게
검증하기 좋다. 다만 현재 코드에서는 Validator가 실제로 실패해야 하므로, 이 질문 하나만으로
재생성 호출을 보장할 수 없다. Validator 실패는 semantic 검증 실패뿐 아니라 숫자·citation
검증 실패로도 발생할 수 있다.

```powershell
curl.exe --get "$BASE_URL/answer" `
  --data-urlencode "question_id=CLOVA-C6-01" `
  --data-urlencode "question=현대자동차 2025년 3분기 사업부문별 매출을 알려줘."
```

확인 기준:

- `think_trace.supervisor.action`에 `regenerate_answer`가 있었는지 확인한다.
- `think_trace.supervisor.regeneration_attempts == 1`인지 확인한다.
- 재생성이 실제로 발생하면 `answer_generation`이 한 번 더 호출되고 Validator가 다시 실행된다.
- 첫 Validator가 `success`이면 재생성 호출은 발생하지 않은 것이 정상이다.

재생성 호출을 반드시 검증해야 한다면 자연어 질문만으로 판단하지 말고, Validator의
테스트 double이 `validation_failed`를 반환하도록 한 단위·통합 테스트를 사용해야 한다.

## 5. 실행 결과 기록 양식

각 질문은 다음 항목을 기록한다.

| 항목 | 기록 내용 |
| --- | --- |
| question_id | 요청에 사용한 ID |
| feature flags | 실행 당시 네 개의 CLOVA 관련 플래그 |
| expected operation | 예상한 API operation |
| actual trace | `think_trace`의 관련 부분 |
| provider fallback | provider 오류·rate limit·deterministic fallback 여부 |
| final status | 최종 Validator 상태와 답변 모드 |

특히 `C1`은 public `think_trace`만으로 호출 여부를 확정할 수 없고, `C2`·`C3`·`C5`·`C6`은
각각 `planner=llm`, `answer_mode=hyperclova_x`, `reranker=clova`,
`regenerate_answer`를 호출 증거로 사용한다.

## 6. 근거 코드

- `integration/composition.py`: 환경 플래그와 각 CLOVA client의 주입 조건
- `integration/clova.py`: Chat API endpoint와 `answer_generation`, `semantic_validation`,
  `calculation_planning` operation
- `integration/reranker.py`: CLOVA reranker API
- `interpreter/pipeline/build_intent.py`: 규칙으로 채우지 못한 슬롯만 LLM으로 보충하는 조건
- `interpreter/llm/slot_filler.py`: 슬롯 보충 schema와 허용 값 검증
- `reasoner/deterministic/calculation_planner.py`: 결정적 planner 우선, LLM fallback 조건
- `reasoner/agents/answer.py`: HyperCLOVA X 답변 생성과 deterministic fallback
- `validator/semantic.py`, `validator/node.py`: semantic 검증과 재생성 진입 조건
- `retriever/retrieval.py`: CLOVA reranker 호출 및 fallback trace
- `integration/supervisor.py`: canonical deterministic Supervisor와 `regenerate_answer` 조건
