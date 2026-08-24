# Supervisor 참조형 멀티에이전트 RAG 구현 계획

## 요약

기존 `C:\projects\dis-164\langgraph_rag`는 수정하지 않고, 새 폴더 `C:\projects\dis-164\agentic_rag`에 대회 규칙 기반 멀티에이전트 RAG를 구현한다.

`langgraph-supervisor-py`를 직접 의존하거나 그대로 복사하지 않고, 다음 설계 원칙만 차용한다.

- 명시적인 에이전트 등록 및 라우팅 검증
- 타입이 정의된 handoff 계약
- 공유 상태와 provenance 관리
- 필요한 경우에만 작업 병렬화
- 최소화된 메시지 히스토리
- 구조화된 출력 검증
- 검증된 결과의 불필요한 재생성 방지

## 핵심 구조

```text
질의
  ↓
규칙 기반 질의 정규화
  ↓
메타데이터·의도 1차 판별
  ↓
확신도 부족 시 HyperCLOVA X로 구조화된 의도 분석
  ↓
Supervisor 라우터
  ├─ 단순 조회
  ├─ 기업 비교
  ├─ 재무 계산
  ├─ 이벤트·공시 연결
  ├─ 근거 추출
  └─ 지원하지 않는 질문
  ↓
검색·재랭킹
  ↓
전문 에이전트 처리
  ↓
근거 및 대회 규칙 검증
  ↓
  ├─ 단순 조회·계산 → 결정론적 템플릿 답변
  └─ 복합 근거·인용 필요 → RAG Reasoning API
                         ↓
                    승인된 검색 함수
                    및 tool 메시지
                         ↓
                    인용 포함 답변
  ↓
최종 답변
```

새 폴더는 기존 그래프의 상태·오케스트레이션에는 의존하지 않는다. 다만 문서 분할, 임베딩, 벡터 검색, 재랭킹 등 안정적인 검색 인프라는 `agentic_rag/infrastructure`의 어댑터를 통해 기존 구현과 재사용한다.

## Supervisor 참조 원칙 적용

- 에이전트는 이름과 역할을 registry에 명시적으로 등록한다.
- 시작 시 중복 에이전트명, 존재하지 않는 handoff 대상, 잘못된 라우팅을 검증한다.
- handoff는 자유로운 문자열이 아니라 `target`, `task`, `reason`, `evidence`, `trace`를 포함한 구조화된 계약으로 제한한다.
- 등록되지 않은 에이전트로의 이동은 즉시 거부한다.
- 각 에이전트 결과에는 출처, 검색 문서, 계산 결과, 신뢰도, 처리 주체를 기록한다.
- 에이전트가 공유 상태를 임의로 덮어쓰지 않도록 결과 영역을 분리한다.
- 단순 조회나 이미 검증된 구조화 결과는 최종 에이전트가 다시 표현하지 않고 그대로 전달할 수 있도록 한다.
- 메시지 히스토리는 기본적으로 필요한 근거와 직전 결과만 전달하고, 복잡한 추론이 필요한 경우에만 전체 히스토리를 사용한다.
- 에이전트명과 역할을 메시지에 포함해 관찰 가능성을 높인다.
- LLM 호출 전에는 입력 길이 정리·요약을 수행하고, 호출 후에는 구조·근거·정책 검증을 수행한다.
- 기업별 독립 검색처럼 병렬화가 안전한 작업만 `Send` 기반으로 병렬 처리한다.
- 병렬 에이전트의 답변이 경쟁하지 않도록 결과 병합 순서를 결정적으로 고정한다.
- supervisor가 매번 자유문장으로 판단하지 않고, 규칙 기반 라우터를 우선 사용한다.
- RAG Reasoning API는 모든 요청에 사용하지 않고, 복수 근거·검색 함수 선택·출처 index 인용이 필요한 경우에만 사용한다.
- RAG Reasoning API에 등록하는 검색 함수는 대회에서 승인된 로컬 corpus 검색만 허용한다.
- tool call은 복합 검색·복수 근거·인용 답변에만 제한적으로 사용한다.
- 단순 조회·계산·정책 검증은 tool call 없이 결정론적 경로로 처리한다.
- LLM이 선택할 수 있는 tool은 registry에 등록되고 승인된 local corpus 검색 함수로 제한한다.
- tool 인자, 호출 대상, 검색 결과, 최종 인용을 모두 검증하고 provenance에 기록한다.

`langgraph-supervisor-py` 자체는 런타임 의존성으로 추가하지 않는다. 직접 `StateGraph`를 구성해 대회 제약, HyperCLOVA X 사용 조건, 현재 API 계약에 맞춘다.

## LLM 사용 여부 분리

### LLM을 사용하지 않는 영역

- 질의 기본 정규화
- 기업명·기간·거래소·재무 항목의 규칙 기반 추출
- 검색 필터와 메타데이터 조건 생성
- 임베딩 및 벡터 검색
- 재랭킹 호출 자체
- 수익률·증감률·비율 등 수치 계산
- 근거 문서 존재 여부와 인용 형식 검증
- 지원하지 않는 질문 판별
- 투자 조언·가격 예측 차단
- 재시도·타임아웃·응답 스키마 검증
- 단순 조회 결과의 템플릿 답변 생성

### 일반 HyperCLOVA X를 사용하는 영역

- 문법이 불완전하거나 모호한 자연어 질의의 의도 해석
- 여러 기업·기간·재무 항목의 의미적 연결
- 공시 사건과 관련 문단의 의미 기반 연결
- 검색된 문서에서 필요한 사실의 구조화 추출
- 복수 근거가 아닌 단일 결과의 의미적 요약
- 의도·이벤트·사실의 구조화 추출

### RAG Reasoning API를 사용하는 영역

- 복수 검색 결과를 하나의 근거 기반 답변으로 통합
- 답변 문장에 출처와 source index를 연결
- 복합 질의에서 승인된 RAG 검색 함수 선택
- 검색 결과를 `tool` 메시지로 전달받은 뒤 인용 포함 최종 답변 생성

RAG Reasoning API는 `agentic_rag`에서 전면 제거하지 않는다. 다만 검색·계산·검증을 RAG Reasoning 모델에 위임하지 않고, Supervisor와 결정론적 Agent가 먼저 처리한 뒤 인용 중심 답변 단계에서 조건부로 호출한다.

임베딩과 재랭킹은 모델 API를 사용하지만, 생성형 LLM 사용 영역과는 별도의 검색 모델 영역으로 관리한다.

## 모델·API 선택 정책

초기 테스트 비용을 낮추기 위해 일반 구조화·요약 작업의 기본 모델은 `HCX-DASH-002`로 사용한다. Agent 코드가 모델명을 직접 지정하지 않고 profile을 통해 선택한다.

| 작업 | 기본 경로 | 조건부 경로 |
|---|---|---|
| intent parser | 규칙 기반 | 불확실할 때 Chat Completions v3 + `HCX-DASH-002` |
| retrieval | deterministic 검색 | 사용하지 않음 |
| comparison | deterministic 정렬·비교 | 의미 정렬이 불명확할 때 `HCX-DASH-002` |
| calculation | 계산 계획이 명확하면 Python 실행 | 모호·복합 계산 계획만 `HCX-DASH-002`로 구조화 |
| event linker | deterministic 후보 연결 | 모호할 때 `HCX-DASH-002` |
| fact extractor | 검색 문서 기반 추출 | 구조화 추출이 불충분할 때 `HCX-DASH-002` |
| answer generator | 템플릿 전달 | 복합 근거·인용 필요 시 RAG Reasoning API |
| evidence validator | 코드 검증 | 사용하지 않음 |

### Chat Completions v3 profile

```text
DEFAULT_PROFILE = {
  "model": "HCX-DASH-002",
  "max_tokens": 512,
  "temperature": 0.1,
}
```

공통 모델 interface는 다음 형태로 유지한다.

```python
class ChatModelPort:
    def generate_text(self, messages, *, profile): ...
    def generate_json(self, messages, *, schema, profile): ...
```

고품질 전환 profile은 다음과 같이 정의한다.

```text
QUALITY_PROFILE = {
  "model": "HCX-007",
  "structured_outputs": True,
}
```

Agent는 모델명을 직접 지정하지 않고 profile만 전달한다. 기본 경량 모델, 품질 모델, 향후 fine-tuned 모델을 Agent business logic 변경 없이 교체할 수 있어야 한다.

초기 JSON 처리는 JSON 출력 prompt, code fence 제거, 로컬 JSON/schema 검증, 1회 재시도, deterministic fallback 순서로 구현한다. 이후 품질 모드에서 `HCX-007`과 Structured Outputs를 profile 교체로 적용할 수 있도록 interface를 분리한다.

### RAG Reasoning profile

RAG Reasoning 호출은 인용 중심 답변에만 허용한다.

- endpoint: `/v1/api-tools/rag-reasoning`
- `tools`: 승인된 local corpus 검색 함수만 등록
- `toolChoice`: 복합 질의에서는 `auto`, 단순히 검증된 검색 결과를 답변화할 때는 도구 재검색을 방지하는 명시적 흐름 사용
- 검색 결과는 `role: tool` 메시지의 `search_result` 배열로 전달
- 각 결과에는 `id`와 원문 `doc`를 포함
- 최종 답변의 인용 source index를 문서 ID와 대조 검증
- 검색 결과가 없거나 근거가 부족하면 호출하지 않고 fallback

RAG Reasoning은 일반 Chat Completions를 대체하는 공통 모델이 아니라, 검색 도구 호출과 인용 답변에 특화된 별도 경로다. 따라서 Agent별 호출 조건과 provenance를 분리 기록한다.

### tool call 적용 기준

| 작업 | tool call | 처리 방식 |
|---|---:|---|
| 단순 문서 조회 | 사용하지 않음 | deterministic retriever + 템플릿 답변 |
| 재무 계산 | 사용하지 않음 | 검색된 수치 + Python 계산 |
| 근거·정책 검증 | 사용하지 않음 | 코드 기반 검증 |
| 복합 질의 분해 | 조건부 사용 | 승인된 검색 function 선택 |
| 기업·기간 복수 비교 | 조건부 사용 | 필요한 경우 대상별 검색 후 tool 메시지 전달 |
| 복수 문서 인용 답변 | 조건부 사용 | RAG Reasoning으로 인용 답변 생성 |

tool call은 LLM이 검색 함수를 선택하고, 검색 결과를 `role: tool` 메시지로 전달받아 추가 판단할 수 있다는 장점이 있다. 반면 호출 횟수·비용·검증 복잡도가 증가하므로 Supervisor가 복합 근거 필요성을 확인한 경우에만 활성화한다. LangGraph의 `Send` 기반 병렬 실행과 CLOVA API의 tool call은 서로 다른 기능이며, 각각 그래프 병렬화와 LLM 함수 호출을 담당한다.

## 저비용 호출 정책

- intent confidence가 충분하면 LLM 호출을 생략한다.
- event/fact extraction은 검색 문서가 있고 deterministic 결과가 불충분할 때만 호출한다.
- comparison은 결정론적 검색·정렬을 우선하고, 의미 정렬이 필요한 경우에만 일반 Chat Completions를 호출한다.
- calculation Agent는 항상 Python 계산을 우선한다.
- 검색 결과가 없으면 일반 LLM과 RAG Reasoning 모두 호출하지 않는다.
- 최종 답변은 근거 문서가 있을 때만 생성한다.
- 단순 조회·계산은 template 또는 검증된 구조화 결과를 그대로 전달한다.
- 복합 근거·인용이 필요한 경우에만 RAG Reasoning을 호출한다.
- 동일 질의·동일 context는 로컬 cache를 사용한다.
- 입력 context와 출력 토큰을 제한한다.
- 외부 API retry 횟수는 최소화한다.
- 실패 시 deterministic fallback을 반환한다.

## fallback 사유 및 대체 문서 제공 정책

근거가 부족한 경우에도 답변을 한 줄로 끝내지 않는다. `agentic_rag`는 최종 답변을 만들기 전에 fallback 사유와 대체 문서를 결정론적으로 구성한다.

```text
근거 검증 실패 또는 정책 차단
  ↓
fallback reason code·설명 확정
  ↓
agentic_rag 전용 AlternativeFinder
  ├─ 같은 기업의 다른 기간 문서
  └─ 같은 기간의 유사·다른 기업 문서
  ↓
대체 문서 정렬·중복 제거
  ↓
사유 + 참고 문서 + 직접 근거가 아님을 명시한 fallback 답변
```

- fallback에는 `정책 차단`, `기업·기간·문서 유형 불일치`, `검색 결과 없음`, `관련성 부족`, `필수 수치 누락`, `외부 API timeout·실패` 중 가능한 구체적인 사유를 포함한다.
- 같은 기업의 다른 기간 자료와 같은 기간의 유사·다른 기업 자료를 승인된 local corpus에서만 검색한다.
- 대체 문서는 원 질문의 직접 근거로 사용하지 않으며, 답변에 참고 자료임을 명시한다.
- 대체 문서 검색은 LLM이나 외부 검색을 사용하지 않고 metadata filter, score, source/chunk ID로 처리한다.
- 결과는 문서 ID·기간·source를 기준으로 중복 제거하고 결정론적으로 정렬한다.
- 대체 문서가 없을 때도 “현재 색인에서 확인 가능한 관련 참고 자료가 없음”을 명시한다.
- `alternative_documents`는 공유 상태와 응답 trace에 기록하되 `retrieved_context`의 직접 근거 목록과 분리한다.

구현 계약:

- `AlternativeFinder`는 `agentic_rag` 내부에 소유하며 `retriever.search()`와 `corp_names()` 같은 공개 adapter 계약만 사용한다.
- 기존 `rag/`와 `langgraph_rag/`의 fallback·finder 구현을 직접 import하지 않는다.
- Finder 결과는 `same_company`, `same_period` 두 영역과 문서 ID·source·period·score를 포함한다.
- fallback formatter는 `reason`, `same_company`, `same_period`를 받아 사유와 참고 문서를 함께 출력한다.
- finder 실패는 fallback 자체를 실패시키지 않고 빈 대체 문서와 trace로 안전하게 처리한다.

완료 조건:

- [ ] 모든 fallback 경로에서 구체적인 사유가 출력된다.
- [ ] 같은 기업 다른 기간·같은 기간 다른 기업 문서 탐색이 실제 graph/API 경로에서 호출된다.
- [ ] 대체 문서가 직접 근거로 오인되지 않도록 응답과 상태에서 분리된다.
- [ ] 정렬·중복 제거·finder 실패·대체 문서 없음 테스트가 통과한다.
- [ ] 대체 문서 정책과 실행 결과가 이 문서에 기록된다.

## 계산 Agent 정책

계산 Agent는 특정 재무 항목과 증감률만 처리하는 고정 정규식 계산기가 아니다. 사용자 질의의 계산 대상·기간·지표·연산 순서는 다양할 수 있으므로, LLM은 계산 계획만 구조화하고 실제 계산은 안전한 Python registry가 수행한다.

```text
자연어 계산 질의
  ↓
계산 계획 생성
  ├─ 명확한 질의 → deterministic 계획
  └─ 모호·복합 질의 → HCX-DASH-002 구조화 출력
  ↓
계산 계획 schema·whitelist 검증
  ↓
지표·기업·기간 검색
  ↓
단위·기간·근거 검증
  ↓
등록된 Python 계산 함수 실행
  ↓
재검산·provenance 기록
```

LLM은 Python 코드를 생성하거나 실행하지 않는다. 반환값은 `operation`, `targets`, `metric`, `periods`, `sub_operations`를 포함하는 계산 계획 JSON이다. 명확한 계산은 LLM 없이 바로 실행하고, 모호하거나 복합적인 계산만 일반 Chat Completions로 계획을 구조화한다.

허용 연산은 registry에 등록한다.

- 기본 연산: `add`, `subtract`, `multiply`, `divide`
- 변화율: `absolute_change`, `percentage_change`, `cagr`
- 비율: `margin`, `ratio`, `debt_ratio`, `current_ratio`
- 집계·비교: `sum`, `average`, `min`, `max`, `rank`, 기업 간 차이
- 복합 연산: 허용된 연산으로만 구성된 AST 또는 계산 DSL

임의 Python expression, `eval`, shell 명령, LLM 생성 코드 실행은 금지한다. 실행 전 지표 존재 여부, 기업·기간 일치, 단위 변환, 분모 0, 누락값, 분기·연간 혼합 여부를 검증하고, 실행 후 입력값·계산식·결과·근거를 재검산해 provenance에 기록한다.

### 확장 계산 DSL/AST 및 미지원 계산 정책

위 목록에 없는 계산도 LLM이 기존 primitive를 조합한 JSON AST/DSL로 표현할 수 있으면 실행한다. LLM은 다음과 같은 계산식을 생성할 수 있지만 Python 소스 코드는 생성하지 않는다.

```json
{
  "op": "divide",
  "args": [
    {"op": "subtract", "args": [{"variable": "current_assets"}, {"variable": "inventory"}]},
    {"variable": "current_liabilities"}
  ]
}
```

허용 primitive는 산술(`add`, `subtract`, `multiply`, `divide`, `power`), 통계(`sum`, `average`, `median`, `min`, `max`), 비교(`greater_than`, `less_than`, `rank`, `difference`), 조건(`if`, `threshold`), 기간(`period_change`, `rolling_average`)처럼 registry에 등록된 함수로 확장한다. AST validator는 연산명·인자 수·변수명·중첩 깊이·숫자 타입을 검증하고, executor는 registry 함수만 호출한다.

계산이 기존 primitive 조합으로 표현되지 않거나 필요한 지표·근거가 없으면 임의 코드를 생성·실행하지 않고 `unsupported_calculation` fallback을 반환한다. 새로운 계산 함수를 추가할 때는 registry 함수, schema, 근거 검증, 단위·기간 규칙, 테스트를 함께 추가한다. 별도 sandbox에서의 동적 코드 실행은 초기 구현 범위에 포함하지 않는다.

## 초기 JSON 처리 방식

`HCX-DASH-002`에서는 초기 단계에 Structured Outputs를 사용하지 않는다.

1. JSON만 출력하도록 prompt를 작성한다.
2. Markdown code fence를 제거한다.
3. JSON을 parse한다.
4. 필수 필드·enum·confidence를 로컬 schema로 검증한다.
5. 실패하면 1회 재시도한다.
6. 재실패하면 deterministic fallback으로 전환한다.

이후 `HCX-007` 품질 profile을 선택하면 동일 schema를 `responseFormat`에 전달한다. Agent business logic은 변경하지 않는다.

## Fine-tuning 전략

초기 구현에서는 fine-tuning을 사용하지 않는다. 먼저 다음 데이터를 수집한다.

- 실제 질의와 intent 정답
- event 연결 정답
- fact extraction 정답
- 기업·기간 비교 정답
- fallback 정답
- 모델 실패 사례와 원인

Prompt 개선만으로 해결되지 않는 반복 오류가 확인된 뒤에만 역할별 fine-tuning을 검토한다.

- Agent별 task ID를 분리한다.
- `model_name`과 `task_id`를 profile에서 관리한다.
- fine-tuned 모델 실패 시 `HCX-DASH-002`로 fallback한다.
- 평가 결과가 baseline보다 나쁘면 활성화하지 않는다.

## 조건부 RAG Reasoning 구현 체크리스트

- [x] `rag_reasoning_client.py`를 일반 Chat client와 별도 모듈로 구현
- [x] 승인된 local corpus 검색 function schema 정의
- [x] RAG Reasoning의 1차 function call 응답에서 tool call 추출
- [x] 승인된 검색 함수만 실행
- [x] tool call 대상과 인자를 schema로 검증
- [x] 검색 결과를 문서 ID·원문을 포함한 `role: tool` 메시지로 변환
- [x] tool 메시지를 포함한 2차 RAG Reasoning 호출로 인용 답변 생성
- [x] 최종 인용 source index를 실제 문서 ID와 대조
- [x] 검색 결과 없음·근거 부족·API 실패 시 deterministic fallback
- [x] 단순 조회·계산 경로에서 RAG Reasoning 호출이 생략되는지 검증
- [x] 호출 모델·tool call·문서 ID·인용·토큰 사용량을 provenance에 기록

완료 조건은 “RAG Reasoning client 파일이 존재한다”가 아니라, 복합 인용 질의가 실제 그래프에서 승인된 검색 function과 `tool` 메시지를 거쳐 인용 검증을 통과하는 것이다.

## 새 디렉터리 구성

```text
C:\projects\dis-164\agentic_rag\
├─ api.py
├─ service.py
├─ state.py
├─ graph.py
├─ contracts.py
├─ registry.py
├─ handoff.py
├─ router.py
├─ agents\
│  ├─ intent_parser.py
│  ├─ retrieval.py
│  ├─ comparison.py
│  ├─ event_linker.py
│  ├─ fact_extractor.py
│  ├─ calculation_planner.py
│  ├─ calculation_validator.py
│  ├─ calculation_executor.py
│  └─ answer_generator.py
├─ deterministic\
│  ├─ metadata.py
│  ├─ calculations.py
│  ├─ calculation_registry.py
│  ├─ calculation_schema.py
│  ├─ calculation_dsl.py
│  ├─ evidence.py
│  └─ policy.py
├─ llm\
│  ├─ chat_clova_x.py
│  ├─ rag_reasoning_client.py
│  ├─ model_profiles.py
│  ├─ prompts.py
│  ├─ schemas.py
│  ├─ cache.py
│  └─ history.py
├─ infrastructure\
│  ├─ segmentation_adapter.py
│  ├─ embedding_adapter.py
│  ├─ vector_store_adapter.py
│  └─ reranker_adapter.py
├─ ingestion\
└─ tests\
```

## API 및 상태 계약

기존 대회 API 형식을 유지한다.

- `GET /answer`
- 입력 질의 처리
- `retrieved_context`, `think_trace`, `answer`는 문자열 형식 유지
- 답변에는 가능한 경우 문서 근거와 인용 포함
- 모든 요청은 request-scoped thread ID 사용
- handoff 횟수와 그래프 recursion을 제한
- 300초 timeout, 재시도, 실패 시 안전한 fallback 적용

공유 상태에는 최소한 다음 정보를 포함한다.

- 원본 질의와 정규화 질의
- 의도와 의도 확신도
- 기업·기간·항목 메타데이터
- 선택된 에이전트
- 검색 문서 및 근거
- 계산 결과
- handoff 이력
- 검증 상태
- 최종 답변
- `think_trace`

## 대회 규칙 준수

- 제공된 데이터와 문서만 사용한다.
- 뉴스, 위키, 외부 검색, 실시간 OpenDART 호출은 사용하지 않는다.
- 생성형 모델은 HyperCLOVA X만 사용한다.
- 투자 조언과 주가 예측은 생성하지 않는다.
- 근거 없는 답변은 거부하거나 제한적으로 응답한다.
- 수치 계산은 코드로 처리한다.
- 모든 에이전트가 독립적으로 임의의 외부 정보를 생성하지 못하도록 검색 근거를 필수 입력으로 둔다.

## 구현 단계

1. `agentic_rag`의 상태·계약·에이전트 registry·handoff 구조를 만든다.
2. 기존 검색 인프라를 새 어댑터 인터페이스로 연결한다.
3. 규칙 기반 질의 분석과 결정론적 라우팅을 구현한다.
4. LangGraph `StateGraph`와 조건부 edge를 구성한다.
5. `Chat Completions v3` 기반 `HCX-DASH-002` client와 공통 `ChatModelPort`를 추가한다.
6. 일반 Agent의 intent/event/fact/answer 호출을 새 Chat client로 조건부 연결한다.
7. 복합 근거·인용 답변 단계에만 별도 RAG Reasoning client와 승인된 검색 function을 연결한다.
8. 모델 profile·cache·context/token 제한과 Agent별 비용·latency·호출 횟수 기록을 추가한다.
9. 전문 에이전트별 결과와 provenance를 통합한다.
10. 근거 검증, 정책 검증, fallback, 답변 생성을 연결한다.
11. `HCX-007` Structured Outputs 교체 profile을 추가한다.
12. API와 평가 시나리오를 추가한다.
13. 기존 `rag/`, `langgraph_rag/`가 변경되지 않았는지 확인하고 새 구조만 검증한다.

## 테스트 계획

- 중복 에이전트명 등록 거부
- 존재하지 않는 handoff 대상 거부
- 잘못된 구조화 LLM 출력 검증
- handoff 횟수·recursion 제한
- 에이전트 결과 provenance 보존
- 검증된 결과의 재생성 없는 전달
- 최소 히스토리와 전체 히스토리 모드 검증
- 병렬 검색 결과의 결정적 병합
- 기업 비교·기간 비교·재무 계산 테스트
- 명확한 계산 질의의 LLM 없는 실행 테스트
- 모호·복합 계산 질의의 구조화 계산 계획 테스트
- 계산 계획 schema·whitelist·AST 검증 테스트
- 영업이익률·순이익률·부채비율·CAGR·기업 비교 계산 테스트
- 단위 불일치·기간 불일치·누락값·0으로 나누기·분기/연간 혼합 테스트
- 임의 Python 코드·`eval`·외부 명령 실행 거부 테스트
- 계산 입력값·공식·결과·근거 provenance 테스트
- 근거 부족·외부 정보 요청·투자 조언 요청 fallback 테스트
- HyperCLOVA X 실패 및 timeout 테스트
- 단순 경로에서 RAG Reasoning endpoint가 호출되지 않는지 검사
- 기본 profile의 모델이 `HCX-DASH-002`인지 검사
- API payload의 model·token 제한 검사
- JSON parse 실패·필수 필드 누락·잘못된 enum 테스트
- intent confidence가 높을 때 LLM을 호출하지 않는지 테스트
- 검색 결과가 없을 때 일반 LLM과 RAG Reasoning을 호출하지 않는지 테스트
- 계산 Agent가 LLM 없이 동작하는지 테스트
- `HCX-DASH-002` timeout·retry·fallback 테스트
- cache hit 시 API 호출이 생략되는지 테스트
- 동일 schema를 `HCX-007` Structured Outputs로 교체할 수 있는지 테스트
- Agent별 비용·latency·호출 횟수 trace 기록 테스트
- `agentic_rag`의 RAG Reasoning API가 인용 중심 경로에서만 호출되는지 테스트
- RAG Reasoning 검색 function이 승인된 local corpus만 대상으로 하는지 테스트
- RAG Reasoning `tool` 메시지의 문서 ID·원문·인용 index 검증 테스트
- 단순 조회·계산에서 RAG Reasoning과 일반 LLM 호출이 모두 생략되는지 테스트
- 가짜 LLM과 가짜 검색 어댑터를 이용한 단위 테스트
- `/answer` 응답 형식 테스트
- 새 코드가 기존 `langgraph_rag`를 직접 import하지 않는 경계 테스트
- 기존 RAG 테스트 회귀 검증

## 저비용 모델 전환 완료 조건

- 기본 일반 LLM 모델은 `HCX-DASH-002`다.
- RAG Reasoning API는 `agentic_rag`에서 제거하지 않고, 인용 중심 복합 답변 경로에서만 조건부 호출된다.
- 일반 LLM이 필요한 Agent만 조건부로 호출된다.
- 검색·실제 계산·검증은 LLM 없이 동작한다.
- 계산 대상과 계획 해석이 필요한 경우에만 계산 planner가 일반 LLM을 조건부 호출한다.
- 계산 실행은 whitelist Python 연산과 검증된 계산 DSL만 사용한다.
- JSON 출력 실패 시 안전한 deterministic fallback이 동작한다.
- `HCX-007`로 profile만 교체해 Structured Outputs 품질 모드로 전환할 수 있다.
- fine-tuning 없이 전체 시스템이 실행된다.
- Agent별 비용·latency·호출 횟수가 기록된다.
- tool call은 registry에 등록된 승인 검색 함수로만 실행된다.
- 단순 조회·계산·검증 경로에서는 tool call이 발생하지 않는다.
- 복합 인용 답변에서는 tool call과 `role: tool` 결과가 provenance에 남는다.
- 명확한 계산은 LLM 없이, 모호·복합 계산은 구조화 planner를 거쳐 실행된다.
- 계산은 whitelist Python 함수 또는 안전한 계산 DSL만 실행한다.
- 기존 `rag/`, `langgraph_rag/`는 수정되지 않는다.

## 구현 진행 현황

기준일: 2026-08-24

완료 표시는 실제 그래프 경로 호출과 자동화 테스트가 확인된 항목에만 적용한다.

### Phase 0 — 기준선 고정 및 진행 관리

- [x] Agentic RAG 테스트 기준선 실행: 20개 통과
- [x] 기존 LangGraph RAG 테스트: 10개 통과
- [x] 루트 테스트: 10개 통과
- [x] `langgraph_rag/` 변경 없음 확인
- [x] 변경 파일과 테스트 결과 기록

완료 기록: `agentic_rag` 20개, `langgraph_rag` 10개, 루트 10개 테스트 통과.

### Phase 1 — 상태·계약·Agent registry

- [x] `state.py` 및 `AgenticState` 분리
- [x] AgentResult·Provenance 계약 구현 및 실제 그래프 전달
- [x] Agent별 handler를 registry에 등록
- [x] 중복 Agent명·잘못된 handoff 대상 검증
- [x] 상태 영역과 provenance 영역 분리

완료 기록: `state.py`, `contracts.py`, `registry.py`, `handoff.py` 및 계약 테스트 통과.

### Phase 2 — 전문 Agent 독립 모듈

- [x] retrieval Agent
- [x] comparison Agent
- [x] calculation Agent
- [x] calculation planner의 명확·모호 질의 분기
- [x] calculation schema·registry·executor·validator 분리
- [x] 복합 계산 계획의 실제 graph 연결
- [x] JSON AST 기본 validator·executor 연결
- [x] 통계·조건·순위·기간 primitive 확장
- [x] 미지원 계산 `unsupported_calculation` fallback
- [x] event_linker Agent
- [x] fact_extractor Agent
- [x] answer_generator Agent
- [x] 공통 specialist 대신 실제 Agent별 graph node 호출
- [x] Agent별 단위 테스트

완료 기록: Agent별 모듈과 graph node를 연결했으며 `test_agents.py`, `test_graph.py`가 통과한다.

### Phase 3 — 프롬프트·구조화 출력·provenance

- [x] intent·event linker·fact extractor·answer prompt 분리
- [x] AgentResult schema 검증
- [x] Agent별 provenance 기록
- [x] 근거 문서 ID와 provenance ID 일치 검증
- [x] 잘못된 구조화 결과·근거 부족 fallback
- [x] 계산 계획 JSON schema와 whitelist 검증
- [x] 계산 입력값·공식·결과·근거 provenance
- [x] AST 연산명·인자 수·변수명·중첩 깊이 검증

완료 기록: `agents/schemas.py`, `deterministic/evidence.py`, `test_schemas.py`, `test_policies.py` 통과.

계산 확장 완료 기록: `calculation_planner.py`, `calculation_schema.py`, `calculation_registry.py`, `calculation_dsl.py`, `calculation.py`, `test_calculation_plans.py`를 연결했다. 명확한 계산은 deterministic planner, 모호한 계산은 조건부 Chat planner, 실제 실행은 whitelist Python registry와 안전한 JSON DSL을 사용한다. 통계·조건·순위·기간 primitive와 `unsupported_calculation` fallback을 검증했다. Agentic 테스트 49개, LangGraph RAG 10개, 루트 10개가 통과했다.

### Phase 4 — handoff 및 병렬 fan-out

- [x] Supervisor의 구조화 handoff
- [x] 비교 질의의 `Send` 기반 기업별 병렬 검색
- [x] 결정론적 문서 병합 및 중복 제거
- [x] 병렬 결과 graph 연결 테스트
- [x] 부분 실패 시 부분 결과 정책의 실제 품질 검증

완료 기록: 비교 fake retriever 기준 병렬 실행·병합·부분 실패 보존 테스트 통과. 실제 corpus 성능 측정은 Phase 10에서 수행한다.

### Phase 5 — 메시지 히스토리 및 LLM 호출 정책

- [x] request-scoped messages 상태
- [x] minimal/full history 정책
- [x] Agent명·역할 metadata 기록
- [x] LLM 입력 context 제한 구조
- [x] 단순 결과의 template 전달
- [x] 복합 근거·인용 질의의 RAG Reasoning 조건부 호출
- [x] 단순 조회·계산·검증 경로의 tool call 생략
- [x] 승인된 local corpus 검색 function 및 `tool` 메시지 연결
- [x] tool 인자·호출 대상·검색 결과 provenance 기록
- [x] Chat Completions v3 + `HCX-DASH-002` client 연결
- [x] `ChatModelPort` 형태의 text/json interface 정의
- [x] model profile·cache·context/token 제한 구조
- [x] JSON prompt·code fence 제거·로컬 schema 검증·1회 재시도
- [x] `HCX-007` Structured Outputs 교체 profile
- [x] 일반 Agent 호출을 새 Chat client로 교체
- [x] RAG Reasoning 답변의 source index·문서 ID 대조
- [x] 복잡한 결과의 history 요약 품질 평가

완료 기록: `llm/history.py`, `llm/chat_clova_x.py`, `llm/model_profiles.py`, `llm/cache.py`, `test_chat_clova_x.py`, `test_rag_reasoning_tool.py` 통과. 단순 경로 생략, tool provenance, 인용 ID, history 요약 검증을 완료했다.

### Phase 6 — 검색·색인 인프라

- [x] segmentation·embedding·vector store·reranker adapter interface
- [x] 로컬 JSON vector 검색
- [x] PostgreSQL + pgvector retriever/writer 코드
- [x] local/PostgreSQL 검색 결과 shape 계약 테스트
- [x] PostgreSQL 연결 healthcheck 계약 테스트
- [x] source metadata·chunk ID 보존
- [ ] 실제 PostgreSQL/pgvector 통합 테스트
- [ ] 동일 corpus local/PostgreSQL 품질 비교

현재 상태: PostgreSQL 코드는 구현했지만 실행 가능한 DSN 기반 통합 테스트가 없어 Phase 6 전체 완료로 표시하지 않는다.

### Phase 7 — ingestion/indexing pipeline

- [x] 승인 corpus 파일 스캔
- [x] PDF·TXT·MD·HTML 판독
- [x] deterministic chunk 생성
- [x] chunk ID·source hash 생성
- [x] local JSON 색인 저장
- [x] PostgreSQL 색인 writer
- [x] `python -m agentic_rag.ingestion.cli` 실행 경로
- [x] 실패 문서 기록 및 성공 문서 계속 처리
- [x] deterministic chunk ID 기반 재실행 계약
- [ ] 실제 CLOVA segmentation을 이용한 corpus 색인 검증
- [x] 색인 실패 문서 기록·재시작 복구 검증
- [ ] PostgreSQL 색인 end-to-end 검증

현재 상태: 로컬 색인 pipeline·실패 문서 기록·결정론적 재실행 계약은 검증했으나 실제 CLOVA segmentation과 운영 DB 검증은 남아 있다.

### Phase 8 — timeout·retry·fallback

- [x] 전체 graph 요청 300초 timeout 경계
- [x] Embedding·Reranker·HyperCLOVA 외부 호출 retry
- [x] exponential backoff
- [x] 최대 retry 횟수
- [x] timeout·retry fallback
- [x] recursion·handoff 제한
- [x] timeout 테스트

완료 기록: `infrastructure/retry.py`, `test_operations.py`, `test_policies.py` 통과.

### Phase 9 — 대회 규칙·근거·답변 검증

- [x] 외부 정보·투자 조언·주가 예측 기본 차단
- [x] 근거 문서 없는 답변 차단
- [x] Agent provenance 및 문서 ID 검증
- [x] 수치 계산 결정론적 처리
- [x] 답변의 모든 수치가 원문과 일치하는지 검증
- [x] 답변 인용 source 실제 존재 여부 검증
- [x] 비교 양쪽 근거 완전성 검증
- [x] event linker 원문 대조
- [x] fact extractor 원문 대조
- [ ] fallback 사유 code·설명 생성
- [ ] 같은 기업 다른 기간 대체 문서 탐색
- [ ] 같은 기간 유사·다른 기업 대체 문서 탐색
- [ ] 대체 문서 정렬·중복 제거 및 직접 근거 분리
- [ ] 대체 문서가 포함된 fallback 응답 테스트

완료 기록: `deterministic/evidence.py`, `test_policies.py`, `test_rag_reasoning_tool.py` 통과. 답변 수치·source/document ID·comparison·event/fact 원문 검증을 연결했다.

### Phase 10 — 전체 평가 및 회귀 검증

- [x] 단순 routing 평가 case
- [x] 비교 routing·병렬 평가 case
- [x] 계산 routing 평가 case
- [x] event linking routing 평가 case
- [x] ambiguous natural-language routing 평가 case
- [x] unsupported fallback 평가 case
- [x] external information·investment advice fallback 평가 case
- [x] Agentic 단위·graph·계약·경계 테스트
- [x] Chat Completions JSON·cache·retry 평가
- [x] RAG Reasoning tool call·인용 검증 평가
- [x] 기존 LangGraph 회귀 테스트
- [x] 기존 루트 회귀 테스트
- [ ] 실제 corpus 기반 lookup/comparison/calculation 평가
- [x] fake HyperCLOVA 오류·timeout·retry 평가
- [ ] 실제 PostgreSQL 평가
- [x] provenance·인용·수치 정확도 평가

현재 상태: 자동화된 구조·계약·정책·fake 외부 API·provenance 평가와 회귀 테스트는 통과했지만 실제 대회 corpus·실제 PostgreSQL·실제 HyperCLOVA 품질 평가는 남아 있다.

### Phase 9 추가 구현 — fallback 사유 및 대체 문서

- [ ] `agentic_rag` 전용 `AlternativeFinder` 계약·구현
- [ ] fallback graph/API 경로에 finder 연결
- [ ] `same_company`·`same_period` 결과를 직접 근거와 분리
- [ ] fallback 사유·대체 문서 수·검색 실패를 `think_trace`와 provenance에 기록
- [ ] 관련 문서가 없을 때의 명시적 응답 처리
- [ ] 전용 단위·graph·API 테스트

완료 기록은 구현 단계별 커밋과 함께 갱신한다.

## 다음 구현 순서

1. 실제 PostgreSQL DSN 기반 retriever/indexer 통합 테스트 실행
2. 실제 PostgreSQL 색인 end-to-end 실행
3. 승인된 대회 corpus로 CLOVA segmentation 및 local/PostgreSQL 품질 비교
4. 실제 corpus 기반 lookup/comparison/calculation 평가
5. 실제 HyperCLOVA 구조화 출력·RAG Reasoning 인용 품질 평가
6. 환경 의존 항목을 검증한 뒤 Phase 6, 7, 10 체크박스 갱신

## 진행 기록 규칙

각 단계의 `[x]`는 코드가 존재한다는 의미가 아니라 실제 graph 호출, 실패 경로, 자동화 테스트, 기존 backend 회귀 검증이 모두 끝났다는 의미로 사용한다.

## 환경 의존 검증 보류 기록

기준일: 2026-08-24

- `AGENTIC_POSTGRES_TEST_DSN`: 미설정
- `POSTGRES_DSN`: 미설정
- `CLOVA_API_KEY`: 미설정
- `RAG_SOURCE_ROOT`: 미설정

현재 환경에서는 PostgreSQL/pgvector 실제 통합·end-to-end 색인, 실제 CLOVA segmentation, 대회 corpus 품질 평가를 실행할 수 없다. 해당 항목은 fake contract·skip 가능한 통합 테스트 경계까지만 구현했으며, 실제 환경변수와 승인 corpus가 제공된 뒤 실행하고 `[x]`로 갱신한다.
