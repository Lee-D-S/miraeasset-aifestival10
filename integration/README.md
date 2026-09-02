# Integration — canonical LangGraph 실행 계층

`integration/`은 Stage1~Stage4와 Supervisor를 하나의 LangGraph로 조립하는 현재 실행 경로다.

## 구성

- `composition.py`: `config.py`가 결정한 Stage2 모드(`local`/`container`)로 pipeline을 조립하는 실행 factory
- `graph.py`: StateGraph, Supervisor 분기, bounded loop
- `supervisor.py`: action contract, deterministic policy, structured LLM adapter
- `tools.py`: allow-listed typed tools와 native `ToolNode` 경계
- `service.py`: `StagePipeline` invoke API와 recursion limit
- `api.py`: `/health`·`/ready`·`/answer`, 제출용 5-field response adapter
- `readiness.py`: `STAGE2_MODE`·container 설정·검색 인덱스의 오프라인 readiness 검사

경로와 backend 선택 지점은 프로젝트 루트의 `config.py` 하나다. `composition.py`는
`config.Stage2Settings.from_env()`가 돌려준 값만 사용하고, 직접 `os.getenv`로 경로를
계산하지 않는다.

## 실행

```python
from integration.composition import build_pipeline

pipeline = build_pipeline()
state = pipeline.invoke(question_id="Q-001", question="질문")
```

기본 factory는 Stage1, Stage2(`STAGE2_MODE` 기본 `local`), Stage3, Stage4를 연결한다. `app.py`는 factory를 import 시 실행하지 않고 `/ready` 또는 `/answer` 요청 시 지연 초기화한다. Stage2는 `intfloat/multilingual-e5-large-instruct`를 사용하며 모델 cache가 없으면 명확히 실패한다. local·container 모두 같은 E5 adapter를 주입하고 문서 text는 raw로 유지한다. 현재 production composition은 실제 A/B의 정보 한계 안전성 gate 실패에 따라 raw query를 사용하며, instruction candidate는 명시적 A/B runner에서만 사용한다. `container` 모드는 `STAGE2_RDB_URL`과 `STAGE2_CHROMA_HOST`가 모두 있어야 하며, 없으면 기동 전에 명확히 실패한다.

`/health`는 프로세스 생존만 확인하고, `/ready`는 pipeline 생성 가능 여부를 확인한다. corpus·DB·provider가 준비되지 않은 경우 `/ready`와 `/answer`는 503을 반환하지만 앱 import와 `/health`는 실패하지 않는다.

NCP 배포 전에는 `python scripts/check_deployment.py`로 네트워크 요청 없이 corpus, CLOVA 설정, SQLite schema·chunk, Chroma collection·ID, manifest와 Stage2 index 범위 일치를 확인한다. 제공 인덱스는 `python scripts/check_real_index.py --allow-partial-index`와 `pytest -m real_index`로 HNSW 차원·대표 검색·read-only 불변성·mock 전체 pipeline을 추가 확인한다.
질의 prefix 채택 여부는 실제 인덱스와 E5 cache가 있는 수동 runner에서
`python scripts/compare_query_instruction.py`로 A/B 검증한다. 이 실행기는 raw baseline과
instruction 후보를 같은 필터·top-k로 비교하고, Recall@20·MRR·정보 한계 fail-closed gate를
기록하며 인덱스를 수정하거나 모델을 다운로드하지 않는다.

공용 State는 `shared_state.py`의 `AgentState`다. `question_id`, `question`, `original_question`은 불변이며 Stage node는 `STAGE_WRITE_FIELDS`에 정의된 partial update만 반환한다. Supervisor는 action과 reason만 결정한다.

```text
START → Stage1 → Supervisor → Stage2 → Supervisor → Stage3 → Supervisor → Stage4 → Supervisor → END
```

compiled graph 시각화는 다음처럼 생성한다.

```python
from integration.graph import StageNodes, build_graph
compiled_graph = build_graph(StageNodes(stage1, stage2, stage3, stage4))
print(compiled_graph.get_graph().draw_mermaid())
```

## 로컬 무비용 E2E

외부 provider 없이 검증할 때는 `integration.testing.build_deterministic_pipeline()`에
Intent, InMemory 문서, deterministic vector score를 주입한다. 이 factory는 테스트 전용이며
production의 semantic fallback으로 사용하지 않는다.

```powershell
pytest tests/test_local_e2e.py
```
