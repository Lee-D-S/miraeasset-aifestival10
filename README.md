# dis-164

AI Festival 2026 공시 질의 응답 Agent 실행 엔진.

## 현재 canonical 구조

```text
app.py                  # FastAPI 진입점
integration/            # LangGraph 조립, Supervisor, API adapter
shared_state.py         # 공용 AgentState와 Stage write contract
stage1/                 # 질의 정규화·Intent·manifest filter
stage2/                 # fixture/hybrid retrieval·embedding·rerank
stage3/                 # Fact·event·계산·답변 초안
stage4/                 # 수치·출처·의미 검증
legacy/                 # 과거 구현과 fixture 보관
tests/                  # 현재 통합 계약 테스트
```

실행 흐름은 `Stage1 → Supervisor → Stage2 → Supervisor → Stage3 → Supervisor → Stage4 → Supervisor`다. Supervisor는 허용된 action만 선택하고, 실제 Stage 작업과 반복 제한은 코드가 담당한다.

## 설치 및 실행

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
python -m pip install -r requirements-dev.txt
uvicorn app:app --reload
```

기본 factory는 `integration/composition.py`에서 Stage1~Stage4를 조립한다. Stage2는
`STAGE2_BACKEND=fixture`와 `STAGE2_BACKEND=sqlite`를 지원한다. fixture 기본값은
`legacy/test_data/disclosure_clova_local.json`이며, `STAGE2_FIXTURE_PATH`로 바꿀 수 있다.
SQLite를 선택하면 `STAGE2_INDEX_PATH`의 chunk·metadata·embedding index를 사용한다.
두 경로 모두 환경변수가 빈 문자열이면 안전한 기본 경로를 사용한다.

`CLOVA_API_KEY` 또는 `CLOVASTUDIO_API_KEY`가 없으면 query embedding은 `embedding_unavailable`로 처리된다. 의미 검증 provider가 없으면 최종 답변을 성공으로 가장하지 않는다. Chroma·PostgreSQL 운영 backend는 후속 작업이다.

실제 SQLite smoke index를 사용하려면 다음처럼 설정한다.

```powershell
$env:STAGE2_BACKEND = "sqlite"
$env:STAGE2_INDEX_PATH = "data/local_smoke/smoke.db"
$env:CLOVA_LLM_ENABLED = "true"  # 답변 생성·semantic validation을 CLOVA로 활성화
uvicorn app:app --reload
```

SQLite adapter는 연결되어 있지만, Chroma·PostgreSQL 운영 adapter는 아직 canonical
factory에 연결하지 않았다.

## API

```text
GET /health
GET /answer?question_id=Q-001&question=질문내용
```

응답은 `question_id`, `question`, `retrieved_context`, `think_trace`, `answer`의 다섯 문자열 필드를 유지한다. `think_trace`에는 Supervisor action과 주요 시도 횟수가 포함된다. Stage1이 생성한 `intent.think_trace`도 `stage1_think_trace`로 함께 기록된다.

## 검색과 안전 제어

```text
Stage1 manifest_filter
→ query embedding
→ keyword search + vector search
→ ID 기준 merge
→ Reranker
→ cited_documents
```

- `question_id`, `question`, `original_question`은 실행 중 불변이다.
- Stage별 partial update는 `STAGE_WRITE_FIELDS`로 검증한다.
- Supervisor 전체 단계는 기본 12회, 검색·planner 재시도는 기본 1회다.
- 답변 재생성은 최대 1회다.
- 잘못된 action, provider 오류, 근거 부족은 fail-closed 처리한다.

## 검증

```powershell
python -m unittest discover -s tests -p "test_*.py"
python -m stage1.tests.run_checks
pytest stage1/tests
python -m compileall -q integration shared_state.py stage1 stage2 stage3 stage4
pytest
```

`legacy/` 문서는 과거 backend의 설계·검증 기록이며, 현재 실행 경로의 기준은 `integration/composition.py`와 `integration/graph.py`다.

## 로컬 무비용 E2E

외부 CLOVA·DB·임베딩 서버 없이 검증하려면 `integration.testing.build_deterministic_pipeline()`에
deterministic Intent와 fixture 문서를 주입한다. 이 factory는 테스트 전용이며 production
fallback으로 사용하지 않는다.

```powershell
pytest tests/test_local_e2e.py
```

실제 CLOVA 호출은 provider 환경변수가 설정된 별도 smoke test에서만 수행한다.

Stage3 답변 생성은 질문과 관련된 Fact를 우선 전달한다. strict grounding client가 핵심
수치 또는 citation ID를 포함하지 않은 답변을 반환하면 deterministic grounding fallback으로
교체하여, lookup 답변에 기업·기간·기준·지표·값·출처 문서ID가 남도록 한다.
