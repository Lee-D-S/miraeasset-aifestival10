# LangGraph RAG 구현 플랜

## 실행 흐름

모든 질문을 공시 DB에서 검색하는 고정형 RAG 흐름을 사용한다. 검색은 서버가 수행하고, 답변 생성 단계에서는 검색 tool을 전달하지 않는다. 공시 근거가 없는 질문은 직접 답변하지 않고 fallback한다.

```text
START → retrieve → rerank → generate_answer → evaluate_groundedness
                                               ├─ grounded → finalize
                                               ├─ not_grounded → rewrite_query → retrieve
                                               └─ not_sure → fallback
```

웹 검색, direct 답변 경로, RAG Reasoning tool loop는 사용하지 않는다. `retrieve`와 `rerank`가 만든 문서를 `generate_answer`에 전달하고, groundedness 평가가 실패한 경우에만 query rewrite 후 재검색한다.

## 모듈 원칙

- 기존 `rag/`와 기존 `app.py`는 수정하지 않는다.
- 각 노드는 다른 노드 모듈을 import하거나 직접 호출하지 않는다.
- 노드 간 데이터 전달은 `GraphState`로만 한다.
- 외부 client와 Vector Store는 Protocol과 adapter로 주입한다.
- 조건부 엣지는 질문 라우팅, 검색 결과, groundedness 결과에 사용한다.

## 품질·안정성

- groundedness 결과는 `grounded`, `not_grounded`, `not_sure`로 제한한다.
- `not_grounded`는 query rewrite 후 제한된 횟수만 재검색한다.
- `not_sure`, API 오류, 검색 결과 없음은 fallback으로 종료한다.
- 로컬 실행은 `InMemorySaver`, 실행에는 `thread_id`와 `recursion_limit`을 사용한다.
- LangGraph 전용 의존성은 `requirements-langgraph.txt`로 분리한다.

## 후속 구현: 모듈별 교체성 강화

현재 구조는 노드와 외부 구현을 Protocol·adapter로 분리했지만, 일부 역할이 하나의 의존성으로 묶여 있다. 다음 단계에서 각 모듈을 독립적으로 교체할 수 있도록 확장한다.

- `answer_generator`, `groundedness_evaluator`, `query_rewriter`용 Protocol을 각각 분리한다.
- `GraphDependencies`의 단일 `llm` 의존성을 역할별 의존성으로 세분화한다.
- 답변 생성, 근거성 평가, 질문 재작성에 서로 다른 LLM·API·일반 Python 구현을 주입할 수 있게 한다.
- Retriever·Reranker·Generator·Evaluator의 입력·출력 DTO와 State 필드 계약을 명시한다.
- 구현체 선택을 `graph.py` 직접 수정 없이 바꿀 수 있도록 factory 또는 설정 기반 registry를 검토한다.
- 각 Protocol에 대응하는 fake 구현체를 두고 모듈 교체 테스트를 추가한다.
- 다음 조합을 독립적으로 검증한다.
  - CLOVA 답변 생성 + 별도 평가 모델
  - PostgreSQL 검색 + 로컬 검색기
  - CLOVA Reranker + 다른 Reranker
- LLM 질문 재작성 + 규칙 기반 질문 재작성

## Reranker usage contract

- `retrieve` may return up to `RAG_RETRIEVAL_TOP_K` candidates.
- `rerank` receives only `RAG_RERANK_TOP_K` candidates.
- The selected `cited_documents` are the only documents passed to `generate_answer` and `evaluate_groundedness`.
- Query-rewrite retries repeat the same retrieval-limit and rerank-limit contract.
- A missing reranker result ends in fallback; it must not produce an uncited answer.
