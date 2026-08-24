# Agentic RAG

`langgraph_rag/`를 수정하지 않고 추가한 Supervisor 참조형 LangGraph backend다. 에이전트 registry, 구조화 handoff, 결정론적 라우팅, 근거 검증을 포함한다.

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
- CLOVA API 키가 있으면 기존 JSON 색인의 벡터 차원에 맞춰 Embedding v2로 질문을 임베딩한다.
- 기존 `rag/`, `langgraph_rag/` orchestration을 import하지 않는다.

## 테스트

```powershell
python -m unittest discover -s agentic_rag/tests -v
```
