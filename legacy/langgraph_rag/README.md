# LangGraph RAG 병렬 구현

이 backend는 `rag/` 패키지를 import하지 않는다. 임베딩·검색·리랭킹·CLOVA 호출·Vector Store 구현은 `langgraph_rag/runtime.py`가 자체 소유한다.

공통 `app.py` 진입점에서 선택할 수 있는 CLOVA 기반 RAG 구현입니다. 기존 `rag/`와 동일한 API 계약을 사용하고, LangGraph의 독립 노드와 조건부 엣지로 실행 흐름을 구성합니다.

## 설치

프로젝트 루트에서 실행합니다.

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
```

## 실행

기존 `app.py`에서 선택할 수 있는 LangGraph 기반 RAG 구현입니다. 별도 FastAPI 진입점은 두지 않습니다.

```powershell
$env:RAG_BACKEND="langgraph"
uvicorn app:app --reload
```

엔드포인트:

- `GET /health`
- `GET /answer?question_id=Q-001&question=질문내용`

환경변수와 Vector Store 설정은 루트 `common/` 설정을 사용합니다. `CLOVA_API_KEY`와 로컬 색인 또는 `POSTGRES_DSN`이 필요합니다.

## 그래프 시각화

```powershell
python -m langgraph_rag.render_graph
```

Mermaid 그래프를 표준 출력으로 확인할 수 있습니다.

## LangGraph 전용 색인 도구

LangGraph backend는 `rag/` 없이도 공시 스캔, 문서 판독, 문단 분할, 임베딩, 로컬 색인, PostgreSQL 색인을 수행할 수 있습니다.

```powershell
python -m scripts.langgraph_inspect_corpus --source "<DATA_DIR>"
python -m scripts.langgraph_build_fake_index --source "<DATA_DIR>"
python -m scripts.langgraph_build_local_index --source "<DATA_DIR>" --limit 1 --max-chunks 3
python -m scripts.langgraph_build_index --source "<DATA_DIR>" --limit 10
python -m scripts.langgraph_search_local_index "질문" --top-k 5 --rerank-top-k 5
```

LangGraph 색인 기본 파일은 `test_data/langgraph_disclosure_clova_local.json`과 `test_data/langgraph_segmentation_cache.json`입니다. 기존 classic RAG 색인 파일과 분리해 backend 간 상태가 섞이지 않도록 했습니다.

## 구조

```text
retrieve → rerank → generate_answer → evaluate_groundedness
                                      ├─ grounded → finalize
                                      ├─ not_grounded → rewrite_query → retrieve
                                      └─ not_sure → fallback
```

모든 질문은 먼저 공시 DB에서 검색합니다. 검색 결과와 리랭킹된 근거가 없으면 답변을 생성하지 않고 fallback합니다. 초기 DB 검색과 리랭킹이 끝난 뒤에는 이미 확보한 문서를 사용해 답변을 생성합니다. 답변 생성 단계에서 검색 tool을 다시 호출하지 않으므로 중복 검색이 발생하지 않습니다. 각 노드는 다른 노드를 import하거나 직접 호출하지 않고 `GraphState`와 주입된 Protocol만 사용합니다. `runtime.py`가 LangGraph backend의 CLOVA client, 임베딩, Vector Store, 검색기, 리랭커를 소유하고 `adapters.py`에서 Protocol에 연결합니다.

## 테스트

외부 CLOVA API를 호출하지 않는 fake 의존성 테스트입니다.

```powershell
python -m unittest discover -s langgraph_rag/tests -v
```

Reranker contract: retrieval can return up to `RAG_RETRIEVAL_TOP_K` candidates, but the reranker receives only `RAG_RERANK_TOP_K`. The selected `cited_documents` are passed to both answer generation and groundedness evaluation. If reranking returns no documents, the graph falls back without generating an uncited answer.

Fallbacks include the reason the exact request failed and, when available, separate reference candidates for the same company in another period or a similar company in the same period. These references are not used as evidence for the original answer.
