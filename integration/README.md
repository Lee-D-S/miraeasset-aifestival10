# Integration — canonical LangGraph 실행 계층

`integration/`은 Stage1~Stage4와 Supervisor를 하나의 LangGraph로 조립하는 현재 실행 경로다.

## 구성

- `composition.py`: 환경변수와 fixture를 이용한 실행 factory
- `graph.py`: StateGraph, Supervisor 분기, bounded loop
- `supervisor.py`: action contract, deterministic policy, structured LLM adapter
- `tools.py`: allow-listed typed tools와 native `ToolNode` 경계
- `service.py`: `StagePipeline` invoke API와 recursion limit
- `api.py`: `/health`·`/ready`·`/answer`, 제출용 5-field response adapter

## 실행

```python
from integration.composition import build_pipeline

pipeline = build_pipeline()
state = pipeline.invoke(question_id="Q-001", question="질문")
```

기본 factory는 Stage1, JSON fixture Stage2, Stage3, Stage4를 연결한다. `app.py`는 factory를 import 시 실행하지 않고 `/ready` 또는 `/answer` 요청 시 지연 초기화한다. Stage2는 CLOVA query embedding provider가 없으면 `embedding_unavailable`를 반환하며, 운영 DB adapter는 아직 연결하지 않는다.

`/health`는 프로세스 생존만 확인하고, `/ready`는 pipeline 생성 가능 여부를 확인한다. corpus·DB·provider가 준비되지 않은 경우 `/ready`와 `/answer`는 503을 반환하지만 앱 import와 `/health`는 실패하지 않는다.

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
Intent, fixture 문서, deterministic vector score를 주입한다. 이 factory는 테스트 전용이며
production의 semantic fallback으로 사용하지 않는다.

```powershell
pytest tests/test_local_e2e.py
```
