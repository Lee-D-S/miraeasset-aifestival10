# LangGraph RAG 병렬 구현

기존 `rag/`와 `app.py`를 수정하지 않고, 같은 CLOVA 기반 RAG 흐름을 LangGraph의 독립 노드와 조건부 엣지로 구현한 버전입니다.

## 설치

프로젝트 루트에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
```

## 실행

기존 API와 분리된 LangGraph API입니다.

```powershell
uvicorn langgraph_app.app:app --reload --port 8001
```

엔드포인트:

- `GET /health`
- `GET /answer?question_id=Q-001&question=질문내용`

환경변수와 Vector Store 설정은 기존 `rag` 구현과 공유합니다. `CLOVA_API_KEY`와 로컬 색인 또는 `POSTGRES_DSN`이 필요합니다.

## 그래프 시각화

```powershell
python -m langgraph_app.render_graph
```

Mermaid 그래프를 표준 출력으로 확인할 수 있습니다.

## 구조

```text
retrieve → rerank → generate_answer → evaluate_groundedness
                                      ├─ grounded → finalize
                                      ├─ not_grounded → rewrite_query → retrieve
                                      └─ not_sure → fallback
```

모든 질문은 먼저 공시 DB에서 검색합니다. 검색 결과와 리랭킹된 근거가 없으면 답변을 생성하지 않고 fallback합니다. 초기 DB 검색과 리랭킹이 끝난 뒤에는 이미 확보한 문서를 사용해 답변을 생성합니다. 답변 생성 단계에서 검색 tool을 다시 호출하지 않으므로 중복 검색이 발생하지 않습니다. 각 노드는 다른 노드를 import하거나 직접 호출하지 않고 `GraphState`와 주입된 Protocol만 사용합니다. 기존 CLOVA client, Vector Store, 검색기, 리랭커는 `adapters.py`에서 연결합니다.

## 테스트

외부 CLOVA API를 호출하지 않는 fake 의존성 테스트입니다.

```powershell
python -m unittest discover -s langgraph_app/tests -v
```

Reranker contract: retrieval can return up to `RAG_RETRIEVAL_TOP_K` candidates, but the reranker receives only `RAG_RERANK_TOP_K`. The selected `cited_documents` are passed to both answer generation and groundedness evaluation. If reranking returns no documents, the graph falls back without generating an uncited answer.
