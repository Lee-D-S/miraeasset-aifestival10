# Stage3

Stage3는 Stage1의 Intent와 Stage2의 구조화된 검색 결과를 받아 Fact를 추출하고,
결정론적 계산·비교·이벤트 연결을 수행한 뒤 근거 기반 답변을 작성하는 단일
LangGraph 노드다.

Stage3는 검색, 임베딩, rerank, 최종 답변 검증, API JSON 생성을 담당하지 않는다.
이 기능들은 각각 Stage2와 Stage4 또는 외부 통합 그래프의 책임이다.

## Deterministic grounding gate

Numeric Facts retain an `aggregation_scope` of `total`, `segment`, `product`,
`region`, or `unknown`. Stage3 selects only the scope requested by the
question; a total-revenue lookup cannot be satisfied by a business-segment
revenue row. The requested company, period, metric, basis, and scope must all
be explicitly present in the Fact or its document metadata. If any required
condition is missing, Stage3 returns `insufficient_evidence` rather than
using an unrelated number.

Korean compound amounts such as `300조 8,709억원` are normalized
deterministically to canonical KRW while retaining the original display text
for grounded answers. `DIS164_STRICT_GROUNDING_V2` controls the gate and is
enabled by default; setting it to `false` is a temporary rollback switch.

외부 4-stage 그래프의 전체 실행 State는 `C:/projects/dis-164/shared_state.py`의
`AgentState`를 사용한다. 이 문서의 `Stage3NodeState`/`Stage3NodeOutput`은 그
전체 State 중 Stage3가 읽고 쓰는 부분 계약이며, Stage3 노드는 전체 State를
직접 재구성하지 않고 partial update만 반환한다.

## Canonical node API

```python
from stage3 import build_stage3_node

stage3_node = build_stage3_node(answer_client=hyperclova_client)
update = stage3_node(state)
```

`build_stage3_node()`가 반환하는 함수는 LangGraph node 규격에 맞춰 state를 하나
받고 partial state update를 반환한다. 입력 state는 직접 수정하지 않는다.

### 입력 state

| 필드 | 설명 |
|---|---|
| `question` | 사용자 원문 질문 |
| `intent` | Stage1 Intent mapping 또는 `Stage3Intent` |
| `route` | `ok`, `need_clarify`, `unanswerable`, `unsafe` |
| `stage2_result` | `documents`/`cited_documents`를 포함한 구조화 검색 결과 |
| `context` | 기존 공유 context. 구조화 문서가 없으면 근거로 위조하지 않음 |
| `messages` | LangGraph 대화 이력 |
| `gen_retry_num` | 답변 생성 횟수 |

통합 그래프에서 Stage3가 기대하는 Intent의 canonical 확장 형태는 다음과 같다.

```json
{
  "question_type": "lookup|compare|calculation|event|text",
  "calculation": {
    "operation": "percentage_change",
    "metric": "revenue",
    "denominator_metric": "revenue"
  }
}
```

현재 Stage1의 `Intent.to_dict()`에는 `question_type`과 중첩 `calculation`이
포함된다. Stage3는 이 명시적 계획을 사용하며, 호환성을 위해 기존 `intent` 값도
읽을 수 있지만 질문 문장을 다시 해석해 계산을 추측하지 않는다.

따라서 `question_type=calculation`인데 `calculation.operation`이 없으면 Stage3는
질문 문구를 분석해 연산을 추측하지 않고 `missing_calculation_plan`을 반환한다.
`question_type=compare`는 Stage1의 비교 판단에 따라 `rank`를 수행한다.

`query_plan`에 독립 subquery가 있으면 각 subquery를 같은 Fact gate와 계산
규칙으로 순차 처리한 뒤, Stage3가 한 번만 최종 답변을 작성한다. 결과의
`subresults`에는 subquery별 상태·facts·계산·citations가 보존된다. 일부
subquery만 근거를 확보한 경우 상태는 `partial_success`이며, 답변에는 확보된
항목과 확인되지 않은 항목을 함께 남긴다.

### Canonical analysis plan

복합 계산에서는 `AgentState.analysis_plan`이 검색 요구사항과 계산 단계의
단일 원본이다. `query_plan`은 독립 검색 projection이므로 계산 DAG로 재해석하지
않는다. `analysis_plan`이 있으면 Stage3는 requirement별 Fact를 모은 뒤 step을
위상순으로 실행하고, 각 중간 결과를 `kind=derived` Fact로 저장한다. derived
Fact에는 원본 `document_id`와 `input_fact_ids`가 남아 Stage4와 citation 생성이
같은 근거 사슬을 사용할 수 있다.

지원되는 plan operation은 기존 whitelist에 `percentage_point_change`를 추가한
범위이며, plan 자체는 등록 metric/operation과 참조·node/depth/map/reduce 한도를
검증한다. 유효하지 않은 plan은 계산하지 않고 근거 부족으로 종료한다.

### 출력 state

process 가능한 요청은 다음 값을 partial update한다.

```python
{
    "answer": "...",
    "context": "검증용 근거·계산 묶음",
    "messages": [assistant_message],
    "gen_retry_num": previous_gen_retry_num + 1,
    "stage3_result": {
        "status": "success|insufficient_evidence|error|need_clarify|unanswerable|unsafe",
        "answer": "...",
        "facts": [...],
        "calculations": [...],
        "comparison_results": [...],
        "linked_events": [...],
        "subresults": [...],
        "citations": [...],
        "warnings": [...],
        "trace": [...]
    }
}
```

`route != ok`이면 Stage3는 `stage3_result`만 기록하고 답변·context·메시지·생성
카운터를 갱신하지 않는다. 최종 차단 문구는 Stage4가 결정한다.

## 내부 처리 흐름

```text
Stage1 Intent + Stage2 structured result
  -> route gate
  -> cited_documents 우선 선택
  -> 본문/XML/HTML에서 Fact 추출
  -> 단위·기간·연결/별도 기준 정규화
  -> Stage1이 지정한 계산·비교 실행
  -> 필요 시 계약·정정·후속 공시 연결
  -> citations와 검증용 context 구성
  -> HyperCLOVA X 답변 작성 또는 deterministic fallback
  -> Stage3 partial state update
```

Fact 추출과 계산은 결정론적으로 실행한다. 지원하는 whitelist operation은
`add`, `subtract`, `multiply`, `divide`, `percentage_change`, `cagr`,
`ratio_percent`, `margin`, `sum`, `average`, `min`, `max`, `rank`다.
입력 기간·단위·통화·연결/별도 기준이 맞지 않으면 계산하지 않고 상태와 경고를
반환한다.

계약·정정·후속 공시 연결은 Stage1이 event/correction 유형을 지정했거나 metric이
계약 관련인 경우에만 실행한다. 원공시 접수번호·문서 ID를 우선 사용하고, 식별자가
없는 경우에도 계약명이 정확히 일치하고 보조 필드가 확인될 때만 연결한다.

## Stage2 boundary

Stage3는 Stage2의 검색·임베딩·rerank 구현을 import하거나 호출하지 않는다.
`stage2_result`는 다음 alias를 지원하는 구조화 mapping이어야 한다.

- 문서 목록: `documents`, `retrieved_documents`, `results`
- 인용 목록: `cited_documents`, `citedDocuments`
- ID: `id`, `doc_id`, `document_id`, `chunk_id`
- 본문: `text`, `content`, `doc`, `text_content`, `page_content`
- 메타데이터: 중첩 `metadata` 우선, top-level 값은 누락 시 보완

`cited_documents`가 있으면 이를 우선 사용한다. ID가 없거나 본문·evidence span이
없는 문서는 Fact와 citation의 근거로 사용하지 않는다. 문자열 `context`만으로
가짜 문서 ID를 만들지 않는다.

## Answer writer

실행 환경에서는 HyperCLOVA X client를 주입한다.

```python
stage3_node = build_stage3_node(answer_client=hyperclova_client)
```

client는 `generate_text(messages)` 인터페이스를 제공해야 한다. client가 없거나
호출에 실패하면 Fact·계산·citation만 사용하는 deterministic fallback을 사용한다.
질문과 직접 관련된 Fact를 우선 선별해 답변 client에 전달하며, strict grounding client가
핵심 수치 또는 citation ID를 빠뜨린 답변을 반환하면 이를 채택하지 않는다. 이 경우
`deterministic_grounding_fallback`이 lookup·text·exists 질의의 답변을 생성한다. 해당
fallback은 기업·기간·기준·지표·값·출처 문서ID를 명시하고, 원문 Fact에 없는 단위는
임의로 추가하지 않는다.
Stage3는 답변 재생성·수치 검증·출처 검증·의미 검증을 수행하지 않는다. 이 작업은
Stage4가 담당한다.

## 기존 API 호환성

`Stage3Service.process()`, `Stage3Service.answer()`, `create_app()`의 호출 형태는
호환용으로 유지된다. 이 API들도 동일한 canonical Stage3 pipeline을 사용한다.

`execution_mode="langgraph"`를 선택하면 `stage3` 하나만 포함한 compatibility graph를
사용한다. 외부 통합 그래프에서는 다음처럼 Stage3 node 하나를 Stage1과 Stage4
사이에 직접 등록한다.

```python
builder.add_node("stage3", build_stage3_node(answer_client=hyperclova_client))
```

LangGraph compatibility graph를 사용하려면 다음 의존성을 설치한다.

```powershell
python -m pip install -r stage3/requirements-langgraph.txt
```

## 테스트

```powershell
python -m unittest discover -s stage3/tests -p "test_*.py" -v
python -m compileall -q stage3
```

단일 node 계약 테스트는 다음을 검증한다.

- partial update와 입력 state 불변성
- 구조화 Stage2 문서·citation 전달
- route 차단과 근거 부족 처리
- Stage1 명시 operation 기반 계산
- 계약·정정 이벤트의 조건부 연결
- HyperCLOVA client 1회 호출과 deterministic fallback
- `gen_retry_num`만 증가하고 `retry_num`과 Stage4 validation은 건드리지 않음

통합 graph에서는 Stage3 canonical validation 결과와 `facts` mirror가 공용
`AgentState`에 전달된다. `correction_mode=include_chain`이면 질문 유형과
관계없이 event linker가 실행된다.

## Multi-period trend calculations

## Process-local parsing·Fact cache

canonical Stage3는 `build_stage3_node(cache=...)`로 pipeline-scoped
`CacheRegistry`를 선택적으로 받는다. Stage2가 전달한 문서는 기존처럼 한 실행 안에서
재사용되고, 반복 실행·multi-query에서는 다음 결정론적 중간 결과를 재사용한다.

- structured evidence: chunk ID + 본문 SHA-256 + parser version
- raw Fact extraction: 문서 hash + extractor version + metric·계산 operation·분모·basis 및
  필요한 company fallback profile

`normalize_facts()`와 계산·비교·citation·AnswerWriter는 매번 현재 intent에 맞게 실행한다.
따라서 cache hit가 답변 계약을 바꾸지 않으며, cache가 없거나 오류가 나면 기존 parser·Fact
추출 경로로 우회한다. Stage3 답변 생성이나 Stage4 검증이 Stage2를 다시 호출하는 구조는
아니며, CLOVA Chat 응답은 cache하지 않는다.

When Stage1 requests more than two periods for `percentage_change` or `cagr`,
Stage3 uses the first and last requested periods for the headline result. The
selected fact for every requested period is also preserved in
`calculations[0].series`, so a deterministic fallback can show the full
period-by-period trend and its citations remain available.

Event linking requires the origin/follow-up relationship first. After that,
only fields named by the question—such as amount, counterparty, or date—are
required in both documents. Missing requested fields produce an explicit
`insufficient_evidence` event instead of silently treating a linked pair as a
complete answer.
