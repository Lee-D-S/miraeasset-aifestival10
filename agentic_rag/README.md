# Agentic RAG

`langgraph_rag/`를 수정하지 않고 추가한 Supervisor 참조형 LangGraph backend다. 에이전트 registry, 구조화 handoff, 전문 Agent node, provenance, 결정론적 라우팅, 근거 검증을 포함한다.

## 실행

루트 API에서 사용:

```powershell
$env:RAG_BACKEND="agentic"
uvicorn app:app --reload
```

독립 실행:

```powershell
uvicorn agentic_rag.api:app --reload
```

## 원칙

- 제공 corpus만 사용하고 외부 검색이나 실시간 OpenDART를 호출하지 않는다.
- 규칙 기반 분류·검색·계산·정책 검증을 우선한다.
- HyperCLOVA X는 모호한 의도 해석과 근거 기반 자연어 생성에만 사용한다.
- 규칙 기반 의도 확신도가 낮을 때만 구조화된 HyperCLOVA intent parser를 호출한다.
- 일반 생성·구조화 호출은 Chat Completions v3의 `HCX-DASH-002`를 기본으로 사용한다.
- 복합 근거·인용 답변에서만 RAG Reasoning의 승인된 local corpus tool call을 사용한다.
- 단순 조회·계산·검증은 LLM과 tool call 없이 결정론적으로 처리한다.
- 계산 질의는 명확하면 deterministic planner, 모호·복합이면 `HCX-DASH-002` 구조화 planner를 사용한다.
- 실제 계산은 whitelist Python registry만 실행하며 LLM이 만든 Python 코드는 실행하지 않는다.
- CLOVA API 키가 있으면 기존 JSON 색인의 벡터 차원에 맞춰 Embedding v2로 질문을 임베딩한다.
- 기존 `rag/`, `langgraph_rag/` orchestration을 import하지 않는다.

## 테스트

```powershell
python -m unittest discover -s agentic_rag/tests -v
```

기본 일반 LLM profile은 `HCX-DASH-002`이며 다음 환경변수로 조정할 수 있다.

```powershell
$env:AGENTIC_LLM_MODEL="HCX-DASH-002"
$env:AGENTIC_LLM_MAX_TOKENS="512"
$env:AGENTIC_LLM_TEMPERATURE="0.1"
```

RAG Reasoning tool call은 registry에 등록된 `local_corpus_search`만 실행한다. 외부 검색이나 실시간 데이터 호출은 하지 않는다.

계산 Agent는 계산 대상·기간·지표·연산을 계산 계획 JSON으로 구조화한 뒤, 원문 근거·단위·기간을 검증하고 등록된 계산 함수로 실행한다. 지원되지 않거나 근거가 부족한 계산은 결과를 생성하지 않고 fallback한다.

로컬 색인 구축:

```powershell
python -m agentic_rag.ingestion.cli --source "<DATA_DIR>" --output "test_data/agentic_rag_local.json"
```

실패한 문서는 기본적으로 `agentic_rag_ingestion_failures.json`에 기록되며, `--failure-log`로 경로를 지정할 수 있다.

PostgreSQL 색인 구축:

```powershell
python -m agentic_rag.ingestion.cli --source "<DATA_DIR>" --postgres-dsn "$env:POSTGRES_DSN"
```

PostgreSQL 통합 테스트는 테스트 전용 DSN을 `AGENTIC_POSTGRES_TEST_DSN`에 설정한 경우에만 실행한다.

비교 질의는 대상별 `Send` fan-out 후 문서 ID 기준으로 결정론적으로 병합한다.
