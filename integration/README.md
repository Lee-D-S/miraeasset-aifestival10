# Integration — canonical LangGraph 실행 계층

`integration/`은 Interpreter~Validator와 Supervisor를 하나의 LangGraph로 조립하는 현재 실행 경로다.

## 구성

- `composition.py`: `config.py`가 결정한 local Retriever backend로 pipeline을 조립하는 실행 factory
- `graph.py`: StateGraph, Supervisor 분기, bounded loop
- `supervisor.py`: action contract, deterministic policy, structured LLM adapter
- `tools.py`: allow-listed typed tools와 native `ToolNode` 경계
- `service.py`: `StagePipeline` invoke API와 recursion limit
- `api.py`: `/health`·`/ready`·`/answer`, 제출용 5-field response adapter
- `failure_response.py`: 차단·근거 부족 답변의 결정론적 이유·재질문 안내와 내부 reason code
- `readiness.py`: `RETRIEVER_MODE`와 검색 인덱스의 오프라인 readiness 검사

경로와 backend 선택 지점은 프로젝트 루트의 `config.py` 하나다. `composition.py`는
`config.RetrieverSettings.from_env()`가 돌려준 값만 사용하고, 직접 `os.getenv`로 경로를
계산하지 않는다.

## 실행

```python
from integration.composition import build_pipeline

pipeline = build_pipeline()
state = pipeline.invoke(question_id="Q-001", question="질문")
```

기본 factory는 Interpreter, Retriever(local), Reasoner, Validator를 연결한다. `app.py`는 factory를 import 시 실행하지 않고 `/ready` 또는 `/answer` 요청 시 지연 초기화한다. Retriever는 `intfloat/multilingual-e5-large`만 사용하며 모델 cache가 없으면 명확히 실패한다. 제공된 SQLite·Chroma 인덱스는 read-only로 열고, `RETRIEVER_MODE`가 `local` 외의 값이면 readiness에서 명확히 실패한다. `e5-instruct`는 active 설정에서 거부한다. `CLOVA_RERANKER_ENABLED`는 답변용 `CLOVA_LLM_ENABLED`와 독립적으로 동작하며, `INTERPRETER_USE_LLM=1`일 때만 unresolved 슬롯에 Chat JSON 보완을 호출한다.

`/health`는 프로세스 생존만 확인하고, `/ready`는 pipeline 생성 가능 여부를 확인한다. corpus·DB·provider가 준비되지 않은 경우 `/ready`와 `/answer`는 503을 반환하지만 앱 import와 `/health`는 실패하지 않는다.

NCP 배포 전에는 `python scripts/check_deployment.py`로 네트워크 요청 없이 corpus, CLOVA 설정, SQLite schema·chunk, Chroma collection·ID, manifest와 Retriever index 범위 일치를 확인한다. 제공 인덱스는 `python scripts/check_real_index.py --allow-partial-index`와 `pytest -m real_index`로 HNSW 차원·대표 검색·read-only 불변성·mock 전체 pipeline을 추가 확인한다.
검색 backend 후보 비교는 `docs/retrieval-experiments.md`의 별도 실험 pipeline에서 수행한다.
Production Reranker는 deterministic 검색 결과를 기본으로 하고, 명시적으로 켠 경우에만
상위 100개를 CLOVA adapter에 보낸다. provider 오류·429·빈 결과는 deterministic 결과로
fallback하며 `suggestedQueries`는 trace에만 남긴다.

답변 불가·근거 부족 상황의 최종 `answer`는 기존 결론 문장을 유지한 뒤 사용자 조치가
가능한 이유와 필요한 경우 재질문 안내를 덧붙인다. 문장은 외부 LLM 추가 호출 없이
결정론적으로 생성하고 약 300자로 제한한다. 내부 `failure_reason_code`는 `think_trace`에
기록하며, 제출 API의 상위 5개 문자열 필드는 변경하지 않는다.

공용 State는 `shared_state.py`의 `AgentState`다. `question_id`, `question`, `original_question`은 불변이며 Stage node는 `STAGE_WRITE_FIELDS`에 정의된 partial update만 반환한다. Supervisor는 action과 reason만 결정한다.

```text
START → Interpreter → Supervisor → Retriever → Supervisor → Reasoner → Supervisor → Validator → Supervisor → END
```

compiled graph 시각화는 다음처럼 생성한다.

```python
from integration.graph import StageNodes, build_graph
compiled_graph = build_graph(StageNodes(interpreter, retriever, reasoner, validator))
print(compiled_graph.get_graph().draw_mermaid())
```

## 로컬 무비용 E2E

## Canonical analysis plan

복합 계산 질의는 `integration.supervisor.build_planner_tool()`이 단일 canonical
`analysis_plan`을 생성한다. `Intent.calculation`은 Interpreter의 단일 계산 seed,
`Intent.query_plan`은 독립 검색 projection으로만 사용한다. `analysis_plan`이
있으면 Retriever는 `requirements`를 검색하고 Reasoner는 `steps`를 실행하며 Validator는
같은 plan과 근거를 재검증한다.

deterministic compiler가 먼저 실행되며, 미해결 계산만
`QUERY_PLANNER_LLM_ENABLED=true`일 때 Clova JSON proposal을 제한적으로 사용한다.
LLM 출력은 등록된 metric/operation, 참조, depth/node/map/reduce 한도를 검증한 뒤
실행한다. 기본값은 false이며 API 제출 응답의 5-field contract는 변하지 않는다.

## Process-local cache

`build_pipeline()`은 `DIS164_CACHE_*` 설정으로 하나의 pipeline-scoped
`CacheRegistry`를 만들고 E5 embedding adapter, local hybrid retriever, Reasoner node에
주입한다. 기본값은 TTL 600초, query embedding 256개, 구조화 문서 512개, Fact 1,024개,
SQL 후보 목록 16개다. `DIS164_CACHE_ENABLED=false` 또는 개별 max 값 `0`으로 우회할 수
있다.

cache 대상은 최종 search query의 E5 embedding, 동일 manifest filter의 SQL 후보 문서,
structured parsing, intent별 raw Fact extraction이다. 최종 ranking·CLOVA Reranker·Chat
응답은 cache하지 않는다. index signature와 코드 version이 key에 포함되고, 값은 호출자
변이를 막기 위해 복사된다. cache 장애는 원래 계산으로 우회하며 운영 통계는 public answer나
think trace에 넣지 않는다. worker별 메모리는 독립적이고 외부 Redis는 필요하지 않다.

외부 provider 없이 검증할 때는 `integration.testing.build_deterministic_pipeline()`에
Intent, InMemory 문서, deterministic vector score를 주입한다. 이 factory는 테스트 전용이며
production의 semantic fallback으로 사용하지 않는다.

```powershell
pytest tests/test_local_e2e.py
```
