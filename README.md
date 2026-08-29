# dis-164

AI 페스티벌 2026 4단계 Agent 실행 엔진.

## 현재 구조

```text
app.py                  # FastAPI 진입점
integration/            # Stage1~Stage4 LangGraph/API 구성
shared_state.py         # 공용 AgentState 계약
stage1/                 # 질의 정규화·Intent·manifest 필터 생성 노드
stage3/                 # 표준 단일 Stage3 노드
stage4/                 # 답변 수치·출처·의미 검증 및 최종화 노드
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
Stage1 → Supervisor → Stage2 → Supervisor → Stage3 → Supervisor → Stage4 → Supervisor → API 응답
             차단/문서 없음 → Stage4                         retry/planner → 제한된 loop
```

Stage3는 `stage3`의 `build_stage3_node()`로 제공하고, 나머지 Stage 노드 함수는
팀 통합 계층에서 주입합니다. 전체 파이프라인은 다음과 같이 구성합니다.

```python
from integration.graph import StageNodes
from integration.service import StagePipeline
from stage1 import build_stage1_node
from stage3 import build_stage3_node
from stage4 import build_stage4_node

pipeline = StagePipeline(StageNodes(
    stage1=build_stage1_node(
        corpus_dir=corpus_dir,
        llm_client=hyperclova_client,
    ),
    stage2=stage2_node,
    stage3=build_stage3_node(answer_client=hyperclova_client),
    stage4=build_stage4_node(validator_client=hyperclova_client),
))
```

Supervisor는 `integration.supervisor`의 제한된 action 계약을 사용한다. 기본값은
결정론적 bounded policy이며, 운영 환경에서는 `build_supervisor_node(client=...)`로
LLM adapter를 주입할 수 있다. 검색 재시도·계산 계획 보완·Supervisor 전체 단계 수에는
상한이 있고, 허용되지 않은 LLM action은 fail-closed 처리한다.

Stage4는 Stage3의 Fact·계산·citation을 결정론적으로 검증한 뒤, 주입된
HyperCLOVA X 클라이언트로 답변 의미를 검증한다. 검증 실패 시 답변을 한 번
재생성하고 재검증하며, 재생성·검증 또는 모델 호출이 실패하면 확인 불가
응답으로 보수적으로 종료한다. `route != ok` 경로는 LLM을 호출하지 않고
고정된 안내문을 반환한다.

Stage1은 `Intent` dataclass를 공용 State에 저장하지 않고 `to_dict()`로 변환해
`intent`와 State-level `route`를 반환한다. `intent`에는 원래 Stage1 분류값과
Stage3용 `question_type`, `calculation.operation`이 함께 포함된다. 코퍼스는
`corpus_dir`, `CORPUS_DIR` 환경변수, 로컬 대회 자료 경로 순서로 탐색한다.

그래프는 `shared_state.py`의 공용 `AgentState`를 사용하며, 각 Stage는 자신의
부분 업데이트(partial update)만 반환합니다. 외부 응답 어댑터는 대회 제출에 필요한 다음 필드만
반환합니다.
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer`.
