# Interpreter — 질의 이해

자연어 공시 질문을 정규화하고 기업·기간·공시 유형·지표·질문 유형을 추출해
Retriever가 사용할 `manifest_filter`를 생성한다. 원문 공시는 읽지 않고
`universe.csv`와 `manifest.jsonl`의 메타데이터만 사용한다.

## 공용 LangGraph 연결

```python
from interpreter import build_interpreter_node

interpreter_node = build_interpreter_node(
    corpus_dir=corpus_dir,
    llm_client=hyperclova_client,  # 선택적 슬롯 보완
    use_llm=True,
)
```

노드는 `AgentState`에서 `question`만 읽고 다음 partial update를 반환한다.

```python
{"intent": intent.to_dict(), "route": intent.route}
```

`intent`의 원래 분류값(`lookup`, `calc`, `compare` 등)은 보존하며,
`question_type`은 Reasoner 계약에 맞춰 canonical 값으로 추가한다. 계산 질문에는
Reasoner whitelist에 포함된 `calculation.operation`과 `metric`을 기록한다. 비중·마진
질의는 `metric_matches`에서 분모를 추론해 `calculation.denominator_metric`에 넣는다.

Guard는 공백·구두점을 무조건 이어 붙여 검색하지 않는다. 원문에서 직접
일치하거나 패턴 내부의 공백만 허용하며, `3분기 사업부문별`의 토큰 경계를
넘어 `기사`가 되는 식의 오탐을 차단한다. 코퍼스 외 기업명도 기본적으로
질의 대상을 의미하지만, `계약상대방`·`발주처`·`인수인`처럼 명시적인 관계
역할 뒤에 온 경우에는 `related_entities`로 기록하고 대상 기업으로
차단하지 않는다.

**제외 조건:** "A를 제외한 <섹터>" 질의는 `exclude_corp_names` / `excluded_corps`로
기록하고, 섹터는 멤버 목록으로 펼쳐 `manifest_filter.corp_names`에 반영한다.
`think_trace`에는 `trace_summary()` 결과가 직렬화되어 state로 넘어간다.

서로 독립적인 지표나 보고서 유형이 한 질의에 함께 등장하면 `query_plan`에
subquery를 생성한다. 기존 `metric`·`manifest_filter`는 호환성을 위해
유지하며, 각 subquery는 자체 지표·계산계획·기간·manifest 필터를 가진다.
비중·마진처럼 두 지표가 하나의 산식에 필요한 경우에는 분리하지 않는다.
`DIS164_QUERY_PLAN_V1`은 기본 활성화이며, 문제 발생 시 `false`로 내려
기존 단일 질의 경로로 rollback할 수 있다.

## 코퍼스 경로

다음 우선순위로 찾는다.

1. `corpus_dir` 인자
2. `CORPUS_DIR` 환경변수
3. 로컬 대회 자료의 `data/3.공시/corpus`

배포 환경에서는 `CORPUS_DIR`에 `universe.csv`와 `manifest.jsonl`이 있는
디렉터리를 지정한다.

## 규칙 기반·LLM 동작

먼저 규칙으로 해석하고, `use_llm=True`이고 unresolved slot이 있을 때만
주입된 HyperCLOVA X 클라이언트의 `generate_json(messages, schema=..., operation="interpreter_slot_fill")`을
호출한다. 모델이 반환한 기업·분류·공시 유형·계산 연산은 universe와 허용
enum을 통과한 값만 반영하며, 호출 실패 시 규칙 결과를 유지한다.

Compose 및 `.env.example`은 `INTERPRETER_USE_LLM=1`로 슬롯 보완을 활성화한다.
명시적으로 `0`을 설정하면 비활성화한다. 라이브러리에서 플래그를 생략하면 기존처럼
환경변수를 따른다. `CLOVA_LLM_ENABLED`와는 독립적인 스위치다.
기업·지표·의도 누락, 모호한 기업, 기간 누락(최신 요청 제외), 계산 연산·비중 분모
누락 시 보완을 시도한다. 단서 없는 기업·기간을 추측하도록 허용하지 않는다.
선택된 기업으로 해소된 모호 표시를 제거하고, 보완 지표의 공시 필터·시간 기준과
계산계획을 갱신한다. 기본 lookup으로 분류됐더라도 규칙이 설명하지 못한 표현이
남으면 의미 검토를 호출한다. 반환값에는 재무 기준·집계 범위·정정 모드·시간 기준과
반기/분기 기준월도 포함된다. 명시적으로 추출한 조건은 보존한다.
LLM 시도 후에도 지표나 조건이 미해결이거나 의미 검토가 실패하면 `need_clarify`로
넘긴다. 기간 자체를 생략한 일반 질문은 최신 자료를 사용한다는 가정을 기록한다.
슬롯 응답은 `CLOVA_INTERPRETER_MAX_TOKENS`(기본 1024)를 사용하며 semantic validation의
토큰 한도와 분리된다. 실패는 로그와 Intent notes에 예외 종류를 기록한다.
`llm_used`는 호출 여부가 아니라 반환값이 실제 반영되었는지를 뜻한다.
호출·응답 상태는 별도 `llm_status`에 기록한다. `question_type`과 `route`는 모델의
임의 값을 받지 않고 검증된 슬롯으로부터 코드가 결정한다.
`latest_only` 검색은 정정본도 포함하고, Fact 추출 전에 식별 가능한 동일 공시의
최신 접수본을 선택한다. 원공시만 요청한 경우에만 `is_correction=False`를 적용한다.

## 검증

```powershell
python -m interpreter.tests.run_checks
```

골드 질의와 별칭·route·기간·필터 불변식을 검사한다.

## 통합 시 주의점

Interpreter은 `intent`와 `route`만 반환하며 검색·계산·답변 검증을 수행하지 않는다. 이후 Supervisor가 제한된 action을 선택한다. 현재 Intent에는 `question_type`과 중첩 `calculation`이 포함되며, Reasoner는 이 명시적 계획을 사용한다.
