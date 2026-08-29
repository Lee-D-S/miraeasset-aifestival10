# dis-164

AI Festival 2026 four-stage Agent engine.

## Current structure

```text
app.py                  # FastAPI entrypoint
integration/            # Stage1~Stage4 LangGraph/API composition
shared_state.py         # shared AgentState contract
stage3/                 # canonical single Stage3 node
tests/                  # shared integration skeleton tests
```

The previous experimental backends, data, scripts, and integration copies are
preserved under `legacy/`. They are not imported by the current entrypoint.

## Run

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
uvicorn app:app --reload
```

`GET /health` is available immediately. `GET /answer?question_id=...&question=...`
remains a deployment skeleton until the team's Stage1, Stage2, Stage3, and Stage4
implementations are injected into `integration.StagePipeline`.

The final graph uses one shared `AgentState` and passes only partial updates
between nodes:

```text
Stage1 → Stage2 → Stage3 → Stage4 → API response
             route != ok → Stage4
```

Stage3 is supplied by `build_stage3_node()` from `stage3`; the other stage
callables are supplied by the team integration layer. A complete pipeline is
constructed as follows:

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

The graph uses the shared `AgentState` from `shared_state.py`; each stage returns
only its partial update. The external response adapter emits only the competition fields:
`question_id`, `question`, `retrieved_context`, `think_trace`, and `answer`.
