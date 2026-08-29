# dis-164

AI 페스티벌 2026 4단계 Agent 실행 엔진.

## 현재 구조

```text
app.py                  # FastAPI 진입점
integration/            # Stage1~Stage4 LangGraph/API 구성
shared_state.py         # 공용 AgentState 계약
stage3/                 # 표준 단일 Stage3 노드
tests/                  # 공용 통합 골격 테스트
```

이전 실험용 백엔드, 데이터, 스크립트와 통합 복제본은 `legacy/` 아래에 보존되어
있습니다. 현재 진입점에서는 `legacy/`의 코드를 import하지 않습니다.

## 실행

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
uvicorn app:app --reload
```

`GET /health`는 바로 사용할 수 있습니다. `GET /answer?question_id=...&question=...`
는 팀의 Stage1, Stage2, Stage3, Stage4 구현을 `integration.StagePipeline`에
주입하기 전까지 실행 골격으로 동작하며, 현재는 503을 반환합니다.

최종 그래프는 하나의 공용 `AgentState`를 사용하고, 각 노드 사이에는 변경된
필드만 partial update로 전달합니다.

```text
Stage1 → Stage2 → Stage3 → Stage4 → API 응답
             route != ok → Stage4
```

Stage3는 `stage3`의 `build_stage3_node()`로 제공하고, 나머지 Stage 노드 함수는
팀 통합 계층에서 주입합니다. 전체 파이프라인은 다음과 같이 구성합니다.

```python
from integration.graph import StageNodes
from integration.service import StagePipeline
from stage3 import build_stage3_node

pipeline = StagePipeline(StageNodes(
    stage1=stage1_node,
    stage2=stage2_node,
    stage3=build_stage3_node(answer_client=hyperclova_client),
    stage4=stage4_node,
))
```

그래프는 `shared_state.py`의 공용 `AgentState`를 사용하며, 각 Stage는 자신의
부분 업데이트(partial update)만 반환합니다. 외부 응답 어댑터는 대회 제출에 필요한 다음 필드만
반환합니다.
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer`.
