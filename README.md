# dis-164

AI Festival 2026 공시 질의 응답 Agent 실행 엔진이다. FastAPI 프로세스가 Supervisor가
제어하는 LangGraph 파이프라인(Interpreter → Retriever → Reasoner → Validator)을 실행한다.

## 환경 구성

Python 3.11 또는 3.12가 필요하다.

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt -r requirements-langgraph.txt -r requirements-dev.txt
```

환경변수는 `.env.example`를 복사해서 채운다. 경로와 Retriever 모드는 모두 프로젝트 루트의
`config.py` 한 곳에서 읽는다. 상대경로는 실행 CWD가 아니라 프로젝트 루트를 기준으로
해석하므로 어느 디렉터리에서 실행해도 동작이 같다.

```bash
cp .env.example .env
```

주요 환경변수는 다음과 같다. 값의 의미는 `.env.example`에 정리되어 있다.

| 환경변수 | 기본값 | 설명 |
|---|---|---|
| `CLOVA_API_KEY` | (없음) | 답변 생성·semantic 검증에 필요. 배포 환경에서만 주입한다. |
| `RETRIEVER_MODE` | `local` | `local` 외의 값은 허용하지 않는다. |
| `RETRIEVER_INDEX_PATH` | `data/local_db/chunk_index.db` | 제공된 SQLite `chunk_index` 위치. |
| `RETRIEVER_CHROMA_PATH` | `data/local_db/chunk_index_chroma` | 제공된 Chroma persistent 인덱스 위치. |
| `RETRIEVER_CHROMA_COLLECTION` | `chunk_vectors` | 인덱스를 만든 시점의 컬렉션 이름과 일치해야 한다. |
| `RETRIEVER_EMBEDDING` | `e5` | 질의 임베딩. `intfloat/multilingual-e5-large` 하나로 고정한다. |
| `RETRIEVER_ALLOW_PARTIAL_INDEX` | `true` | 제공 인덱스 테스트 시에만 사용한다. 스키마·빈 테이블·차원 오류는 계속 실패한다. |
| `CORPUS_DIR` | (자동 탐색) | Interpreter의 `universe.csv`·`manifest.jsonl` 디렉터리. 자동 탐색이 실패할 때만 지정한다. |
| `CLOVA_LLM_ENABLED` | `true` | 답변 생성·semantic 검증 사용 여부. |
| `INTERPRETER_USE_LLM` | `0` | Interpreter의 unresolved 슬롯 보완에 CLOVA Chat 호출 여부. |
| `CLOVA_RERANKER_ENABLED` | `false` | production Reranker. 켤 때만 상위 100개 후보를 전송한다. |

E5 모델은 서버의 fastembed/Hugging Face 캐시에 미리 준비되어 있어야 한다. 기동 시 외부
다운로드는 하지 않으며, 캐시가 없으면 Retriever 초기화가 실패한다. 실행 중 SQLite·Chroma
인덱스에는 쓰지 않는다.

## 실행 명령어

| 목적 | 명령 |
|---|---|
| 개발 서버 실행 | `uvicorn app:app --reload` |
| 배포 전 오프라인 점검 (CLOVA 호출 없음) | `python scripts/check_deployment.py` |
| 전체 테스트 | `pytest` |
| 통합 테스트만 (pytest 없이) | `python -m unittest discover -s tests -p "test_*.py"` |
| 단일 테스트 | `pytest tests/test_retriever.py::TestClass::test_case` |
| 무비용 로컬 E2E | `pytest tests/test_local_e2e.py` |
| Interpreter gold-query·불변식 검사 | `python -m interpreter.tests.run_checks` |
| 문법 검사 (ruff/flake8 없음) | `python -m compileall -q integration shared_state.py interpreter retriever reasoner validator` |
| 그래프 이미지 재생성 | `python scripts/render_graph.py --png integration/graph.png` |
| 제공 인덱스 구조·대표 질의 검증 | `python scripts/check_real_index.py --allow-partial-index` 후 `pytest -m real_index` |

일반 `pytest`는 대용량 인덱스에 의존하지 않으며 `real_index` marker를 자동 제외한다.

## 디렉터리 구조

```text
app.py              # FastAPI 진입점
config.py           # 경로·Retriever 모드 단일 설정 지점
shared_state.py     # 공용 AgentState와 Stage write contract
integration/        # LangGraph 조립, Supervisor, API adapter
interpreter/        # 질의 정규화·Intent·manifest filter
retriever/          # SQLite + Chroma hybrid retrieval·embedding·rerank
reasoner/           # Fact·계산·이벤트 연결·답변 초안
validator/          # 수치·출처·의미 검증
tests/              # 통합 계약 테스트
```

`README.md`, 각 Stage의 `README.md`, `NCP_DEPLOYMENT.md`는 각 Stage 계약의 authoritative
spec이다. `docs/contest-requirements.md`에 대회 요구사항과 평가 API 스키마가 있다.

## 실행 흐름

```text
START → interpreter → supervisor → retriever → supervisor → reasoner → supervisor → validator → supervisor → END
```

- Supervisor는 허용된 action과 이유만 선택한다. 실제 Stage 작업과 반복 제한은 코드가
  담당한다. 기본값은 Supervisor 12단계, 검색 재시도 1회, planner 재시도 1회, 답변
  재생성 1회다.
- 4단계 Supervisor는 하나의 `supervisor` 노드로 합쳐져 있고, `supervisor_phase`를 읽어
  `_PHASE_ROUTES`로 라우팅한다. 알 수 없는 action은 fail-closed 처리한다.
- Stage별 phase 갱신과 write 권한 검증은 각 Stage 노드를 감싸는 미들웨어이며 별도 노드로
  나타나지 않는다.
- Supervisor 정책은 교체 가능하다. 기본은 LLM을 쓰지 않는 `DeterministicSupervisor`이고,
  `StructuredSupervisorClient`는 action JSON만 반환한다.

### 상태 계약

`AgentState`가 유일한 cross-stage 상태다. 각 Stage는 partial update만 반환하고,
`STAGE_WRITE_FIELDS`가 Stage별 쓰기 가능 키를 정의한다. `question_id`, `question`,
`original_question`은 실행 중 불변이다. 재시도는 `search_query`만 바꾸고 `question`은
바꾸지 않는다. 초기 상태는 `make_initial_agent_state()`로만 만든다.

### 그래프 이미지

![integration/graph.py의 LangGraph 상태머신](integration/graph.png)

노드나 엣지가 바뀌면 `python scripts/render_graph.py --png integration/graph.png`로 다시
뽑는다. 손으로 그리지 않는다.

## Retriever

제공된 read-only 인덱스를 쓰는 `local` 모드만 지원한다.

- `manifest_filter`를 SQLite `chunk_index`의 SQL `WHERE`절로 적용하고, 후보는
  최신 공시 우선(`rcept_dt DESC, rcept_no DESC`)으로 정렬한다.
- Chroma persistent HNSW 파일은 query-only로 읽는다. 제공 인덱스는 legacy HNSW 형식이므로
  애플리케이션은 Chroma client를 열지 않고 `chroma.sqlite3`를 `mode=ro`로 읽으며 HNSW
  파일을 직접 검색한다.
- 질의 임베딩은 E5 1024차원 하나로 고정한다. 문서와 질의는 같은 공간을 쓰며 `e5-instruct`는
  허용하지 않는다.

제공 인덱스는 SQLite `chunk_index` 약 1,155,170행과 Chroma `chunk_vectors` 약 800,460개
벡터로 구성된다. 벡터 커버리지와 Interpreter manifest 문서 집합이 완전히 일치하지 않을 수
있으므로, 이 인덱스를 테스트할 때만 `RETRIEVER_ALLOW_PARTIAL_INDEX=true`를 지정한다.
HNSW에 검색 가능한 벡터가 없는 후보는 결과에서 제외한다.

### 검색 파이프라인

```text
Interpreter manifest_filter
→ query embedding
→ keyword search + vector search
→ ID 기준 merge
→ deterministic hybrid rank
→ (선택) CLOVA Reranker
→ cited_documents
```

- 기본 factory는 metadata 필터 후보 최대 1,000개에서 keyword·vector branch를 각각 100개까지
  합치고, hybrid 결과 최대 200개를 Reasoner에 전달한다.
- `CLOVA_RERANKER_ENABLED=true`일 때만 상위 100개를 Reranker에 보내고, 실패하면 전체
  merged 후보의 deterministic 순위로 fallback한다.
- Retriever·Reasoner는 pipeline마다 독립적인 bounded TTL/LRU 메모리 cache를 쓴다. 원본
  인덱스를 대체하지 않으며, cache hit/miss는 사용자 답변이나 `think_trace`에 노출하지 않는다.
  기본 TTL은 600초이고 `DIS164_CACHE_*`로 조정한다.

## Stage 책임

- **interpreter** — 질문을 정규화하고 기업·기간·공시유형·지표·질문유형을 추출해 `intent`,
  `route`, Retriever용 `manifest_filter`를 만든다. `universe.csv`·`manifest.jsonl` 메타데이터만
  읽는다. 기본은 규칙 기반이고 `INTERPRETER_USE_LLM=1`일 때만 unresolved 슬롯에 CLOVA Chat을
  호출한다.
- **retriever** — 검색만 담당한다. 위 파이프라인으로 `cited_documents`를 만들고
  `retriever_result`를 쓴다.
- **reasoner** — cited 문서에서 Fact를 추출하고, Interpreter가 지정한 계산·비교·이벤트 연결을
  whitelist 연산만으로 실행한 뒤 grounded 답변을 만든다. 검색·rerank·검증은 하지 않는다.
  `question_type=calculation`인데 `calculation.operation`이 없으면 질문 텍스트에서 다시
  추측하지 않고 `missing_calculation_plan`을 반환한다.
- **validator** — Reasoner 답변을 수치 → 출처 → 의미 순으로 검증한다. 새 검색이나 Fact 생성은
  하지 않는다. `regenerate_answer`는 최대 1회 답변을 다시 만든 뒤 재검증한다.

## API

```text
GET /health   프로세스 liveness만 확인. corpus·DB가 없어도 응답한다.
GET /ready    pipeline을 지연 초기화하고 실행 준비 상태를 확인한다.
GET /answer?question_id=Q-001&question=질문내용
```

`/answer`는 항상 대회 규격의 다섯 문자열 필드를 반환한다: `question_id`, `question`,
`retrieved_context`, `think_trace`, `answer`. `think_trace`에는 Stage별
`status`/`warnings`/`trace`, Interpreter의 `think_trace`, Supervisor의 action과 시도
횟수가 들어간다. 답변 불가·근거 부족이면 `answer`는 결론 문장 뒤에 결정론적인 이유를
덧붙이고, 내부 `failure_reason_code`는 trace에만 남긴다. 공개 문장은 약 300자로 제한하고
API key·원시 provider 오류·검색 점수는 넣지 않는다.

pipeline이 준비되지 않으면 `/answer`는 503을 반환한다. provider 용량 실패는 429와
`detail.code=PROVIDER_RATE_LIMITED`로, 그 외 예기치 못한 실패는 503과 `PIPELINE_ERROR`로
분류한다.

## 안전 제어

- 잘못된 action, provider 오류, 근거 부족은 fail-closed 처리한다.
- 프로덕션 경로에서 가짜 embedding·LLM·validator를 대신 쓰지 않는다. CLOVA key가 없으면
  Retriever는 `embedding_unavailable`을 반환하고, semantic validator가 없거나 실패하면
  Validator는 통과시키지 않는다.
- Chat·Reranker·Interpreter 슬롯 호출은 하나의 process-local QPM/TPM limiter를 공유하며
  bounded 재시도·timeout을 적용한다. API key와 요청 본문은 기록하지 않고 `x-ratelimit-*`
  헤더만 `last_rate_limit`에 남긴다.
- `DIS164_STRICT_GROUNDING_V2`는 요청 조건과 정확히 일치하는 Fact만 답변·검증에 쓰는
  grounding gate이며 기본값은 `true`다. 대회 평가 전에는 기본값을 유지한다.

## 무비용 로컬 테스트

외부 CLOVA·대용량 DB·임베딩 서버 없이 회귀를 검증하려면
`integration.testing.build_deterministic_pipeline()`에 deterministic Intent와 InMemory
문서를 주입한다. 이 factory와 `InMemoryRetriever`는 테스트 전용이며 production fallback으로
쓰지 않는다.

```bash
pytest tests/test_local_e2e.py
```

실제 CLOVA 호출은 provider 환경변수가 설정된 NCP smoke에서만 한다. CI와
`check_real_index.py` pipeline 검증은 deterministic Reasoner·Validator provider를 쓴다.

## NCP 배포 구조

![instance level architecture](img/instance_level_schema.drawio.png)

단일 EC2 인스턴스 안에 agent app, vector DB, RDB가 함께 들어 있다. 자세한 절차는
`NCP_DEPLOYMENT.md`를 참고한다.
