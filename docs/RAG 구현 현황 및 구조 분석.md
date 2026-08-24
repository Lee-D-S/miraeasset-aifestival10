# RAG 구현 현황 및 구조 분석

분석 대상: `C:\\projects\\dis-164`

기준일: 2026-08-24

이 문서는 현재 구현된 일반 RAG와 LangGraph RAG의 구조, 차이점, 테스트 상태를 정리한 기준 문서다. 이후 구현이 변경될 때마다 실제 코드와 함께 업데이트한다.

## 현재 구현 요약

현재 프로젝트에는 일반 RAG와 LangGraph RAG가 모두 구현되어 있다. 루트 `app.py`에서 환경변수로 백엔드를 선택하며, 두 구현 모두 동일한 API 계약을 제공한다.

```text
app.py
  ↓
application/factory.py
  ├── RAG_BACKEND=classic
  │     └── rag/services/answer_service.py
  └── RAG_BACKEND=langgraph
        └── langgraph_rag/service.py
```

공통 API:

```http
GET /health
GET /answer?question_id=Q-001&question=질문
```

공통 응답 스키마:

```json
{
  "question_id": "...",
  "question": "...",
  "retrieved_context": "...",
  "think_trace": "...",
  "answer": "..."
}
```

## 일반 RAG 구현

위치:

```text
rag/
├── clients/
├── generation/
├── ingestion/
├── retrieval/
├── services/
└── storage/
```

### 처리 흐름

```text
질문
→ CLOVA Embedding v2
→ Vector Search
→ 메타데이터 필터
→ CLOVA Reranker
→ CLOVA RAG Reasoning Tool Call
→ 필요 시 추가 검색
→ 최종 답변
```

핵심 서비스는 `rag/services/answer_service.py`다.

### 주요 기능

- CLOVA Embedding v2 사용
- 로컬 JSON Vector Store 또는 PostgreSQL + pgvector 사용
- 기업명·기간·공시 유형 기반 메타데이터 필터
- CLOVA Reranker 사용
- `RagReasoningClient`를 이용한 tool-call 기반 추가 검색
- 검색 실패 시 같은 기업의 다른 기간·같은 기간의 다른 기업을 참고 자료로 제시
- 검색 근거가 없으면 답변을 생성하지 않고 fallback

일반 RAG는 현재 대회용 MVP의 중심 구현이다.

## LangGraph RAG 구현

위치:

```text
langgraph_rag/
├── graph.py
├── state.py
├── routes.py
├── service.py
├── adapters.py
├── contracts.py
├── checkpoint.py
└── nodes/
    ├── retrieve.py
    ├── rerank.py
    ├── generate_answer.py
    ├── evaluate_groundedness.py
    ├── rewrite_query.py
    ├── fallback.py
    └── finalize.py
```

### 그래프 흐름

```text
START
  ↓
retrieve
  ↓
rerank
  ↓
generate_answer
  ↓
evaluate_groundedness
  ├── grounded       → finalize
  ├── not_grounded   → rewrite_query → retrieve
  └── not_sure       → fallback
```

### 주요 모듈 역할

- `state.py`: 노드 간 상태를 전달하는 `GraphState` 정의
- `graph.py`: `StateGraph` 생성 및 노드·조건부 엣지 연결
- `routes.py`: 검색 결과·reranker·groundedness에 따른 분기
- `contracts.py`: Retriever·Reranker·LLM·AlternativeFinder Protocol 정의
- `adapters.py`: 기존 `rag/` 구현을 LangGraph Protocol에 연결
- `service.py`: 설정에 따라 그래프를 만들고 공통 `AnswerResponse`로 변환
- `nodes/`: 다른 노드 모듈을 직접 호출하지 않고 State와 Protocol만 사용

## 두 구현의 차이

| 항목 | 일반 RAG | LangGraph RAG |
|---|---|---|
| 흐름 제어 | Python 함수 호출 | StateGraph 노드·조건부 엣지 |
| 추가 검색 | RAG Reasoning tool loop | groundedness 실패 시 query rewrite 후 재검색 |
| 답변 생성 | RAG Reasoning이 검색 tool 호출 가능 | 검색 결과를 미리 주고 답변만 생성 |
| 근거 평가 | 별도 groundedness 단계 없음 | 답변 후 groundedness 평가 |
| 재시도 | 모델 tool-call에 의존 | 최대 retry 횟수 명시 |
| 테스트 | generator·factory·fallback 중심 | 그래프·라우팅·노드 계약 중심 |
| 현재 성격 | 기능이 더 풍부한 MVP | 흐름과 검증 지점이 명확한 구조 |

LangGraph RAG는 답변 생성 단계에 검색 tool을 전달하지 않는다.

```text
retrieve와 rerank에서 검색 완료
→ cited_documents만 답변 생성에 전달
→ 답변이 근거 부족이면 query rewrite 후 재검색
```

## 현재 테스트 상태

실행 명령:

```powershell
python -m unittest discover -s langgraph_rag/tests -v
```

현재 확인 결과:

```text
LangGraph 테스트 10개, 일반 RAG 테스트 10개 통과
OK
```

확인한 항목:

- 그래프 컴파일
- 조건부 노드 존재
- 정상 retrieve → rerank → generate → evaluate → finalize 흐름
- 빈 검색 결과 fallback
- groundedness 라우팅
- fallback 대체 문서
- 노드 간 import 경계

루트 `tests/`에도 다음 테스트가 있다.

- answer generator
- application factory
- CLOVA embedding
- CLOVA segmentation
- RAG 평가
- fallback

## 현재 구현의 장점

- 대회 API 계약을 `common/`에 통합
- 일반 RAG와 LangGraph RAG를 동일 API로 비교 가능
- CLOVA API별 client 분리
- 로컬 Vector Store와 PostgreSQL 저장소 교체 가능
- 근거 없는 답변을 fallback으로 차단
- LangGraph 노드 간 의존성을 Protocol과 State로 분리
- fake 의존성 기반 테스트 제공
- Reranker 결과의 `cited_documents`만 답변 생성과 근거 평가에 사용

## 확인 및 후속 개선 사항

### 1. LangGraph 검색 limit 설정

`LangGraphAnswerService`가 `RAG_RETRIEVAL_TOP_K`를 그래프의 retrieve 노드에 전달한다. 따라서 환경변수 검색 제한이 LangGraph 경로에도 적용된다.

### 2. 두 검색 전략의 의도 확인

일반 RAG는 RAG Reasoning tool-call로 추가 검색할 수 있지만, LangGraph RAG는 답변 생성 시 tool을 전달하지 않는다. 최종 제출 때 어느 흐름을 주력으로 할지 결정해야 한다.

### 3. 근거성 평가 모델 분리 가능성

LangGraph의 답변 생성과 groundedness 평가가 현재 동일한 `dependencies.llm`에 의존한다. 향후 역할별 모델·API를 분리하려면 Protocol과 `GraphDependencies`를 확장해야 한다.

### 4. `think_trace` 노출 정책

`think_trace`에는 모델의 `thinkingContent`가 들어갈 수 있다. 대회 API의 감사용 요약으로 사용할 수 있지만, 내부 추론 전체를 외부에 노출하지 않도록 최종 응답 정책을 확정해야 한다.

### 5. Thread ID와 상태 관리

LangGraph 서비스는 `question_id`와 질문 해시를 조합해 thread ID를 만든다. 평가 질의 간 상태 오염 방지에는 유리하지만, 실제 멀티턴 대화용 상태 관리로 사용할지는 별도 검토가 필요하다.

### 6. 운영 저장소 연결

PostgreSQL DSN이 없으면 로컬 JSON 인덱스를 사용한다. 현재는 실제 전체 공시 corpus를 운영 DB에 연결하기 전의 로컬 MVP 검증 단계다.

## 실행 방법

### 일반 RAG

```powershell
$env:RAG_BACKEND="classic"
uvicorn app:app --reload
```

### LangGraph RAG

```powershell
$env:RAG_BACKEND="langgraph"
uvicorn app:app --reload
```

### LangGraph 그래프 시각화

```powershell
python -m langgraph_rag.render_graph
```

### LangGraph 테스트

```powershell
python -m unittest discover -s langgraph_rag/tests -v
```

### 로컬 색인·검색·End-to-End 테스트

```powershell
python -m scripts.build_local_index --source "<DATA_DIR>" --limit 5
python -m scripts.search_clova_local_index "질문" --top-k 3
python -m scripts.run_clova_local_rag "질문" --retrieval-top-k 5 --rerank-top-k 5
```

## 결론

현재 프로젝트는 다음 구조를 갖춘 상태다.

```text
대회 API 서버
├── Classic RAG: 기능 중심·RAG Reasoning tool loop 포함
└── LangGraph RAG: 단계 분리·근거 평가·재검색 흐름
```

현재 상태에서 대회용 주력으로 발전시키기에는 LangGraph RAG 쪽이 흐름과 검증 지점을 명확히 관리하기 좋고, 일반 RAG 쪽은 이미 구현된 CLOVA 검색·생성 기능을 재사용하는 기반 역할을 한다.

## backend 독립성 원칙

현재 `langgraph_rag/`는 `rag/`를 import하지 않는다. LangGraph backend의 임베딩·CLOVA client·Vector Store·검색·리랭킹·대체 문서 탐색은 `langgraph_rag/runtime.py`가 소유한다. 따라서 `rag/` 디렉터리가 없어도 LangGraph backend를 실행할 수 있다.

반대로 일반 RAG는 LangGraph 모듈을 import하지 않으므로 `langgraph_rag/` 디렉터리가 없어도 일반 RAG를 실행할 수 있다. 루트 `application/factory.py`의 backend 선택은 각 분기 내부에서 지연 import된다.

LangGraph 전용 색인·검사·검색 도구는 다음 CLI로 제공한다.

```powershell
python -m scripts.langgraph_inspect_corpus --source "<DATA_DIR>"
python -m scripts.langgraph_build_fake_index --source "<DATA_DIR>"
python -m scripts.langgraph_build_local_index --source "<DATA_DIR>" --limit 1
python -m scripts.langgraph_build_index --source "<DATA_DIR>" --limit 10
python -m scripts.langgraph_search_local_index "질문"
```

이 도구들은 `langgraph_rag/ingestion.py`, `langgraph_rag/indexing.py`, `langgraph_rag/runtime.py`만 사용한다. classic RAG의 `scripts/build_*`와 색인 파일을 공유하지 않는다.

향후 코드 변경 시에는 다음 항목을 이 문서에도 함께 갱신한다.

- 백엔드 선택 방식
- 그래프 노드와 라우팅 흐름
- 검색·리랭킹·생성·근거 평가 계약
- API 응답 구조
- 저장소 및 색인 방식
- 실행 명령
- 테스트 결과
- 대회 요구사항과 관련된 안전성·fallback 정책

## Supervisor 참조형 Agentic RAG

`agentic_rag/`는 기존 `langgraph_rag/`를 수정하지 않고 추가한 세 번째 backend다. `langgraph-supervisor-py`의 직접 의존성은 추가하지 않았으며, 명시적 agent registry, handoff 대상 검증, 구조화된 결과, provenance, 최소 히스토리라는 설계 원칙만 반영했다.

처리 흐름은 규칙 기반 정규화·정책 검증·의도 분류 후 검색과 근거 검증을 수행하고, 필요한 경우에만 HyperCLOVA X로 답변을 생성한다. 현재 구현의 로컬 검색 어댑터는 기존 JSON 색인 형식을 읽으며, 기존 backend orchestration에는 의존하지 않는다.

```powershell
$env:RAG_BACKEND="agentic"
uvicorn app:app --reload
python -m unittest discover -s agentic_rag/tests -v
```

현재 Agentic RAG는 새 backend의 구조·계약·fallback 검증을 위한 초기 구현이다. 낮은 확신도 질의에는 HyperCLOVA 구조화 intent parser를 선택적으로 호출하며, CLOVA API 키가 있으면 질문 임베딩에도 Embedding v2를 사용한다. 기업 비교의 병렬 fan-out, PostgreSQL 전용 adapter, 실제 corpus 품질 평가는 후속 작업이다.
