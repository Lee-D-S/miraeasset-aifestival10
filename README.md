# dis-164

AI Festival 2026 - 퍼스트펭귄

## RAG API scaffold

현재 프로젝트는 CLOVA Studio 기반 RAG 구현을 위한 최소 FastAPI 골격입니다.
최신 CLOVA Studio API 문서를 확인한 뒤 `rag/services/clova_client.py`에 실제 API 호출을 연결합니다.

### Run locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```

### Endpoints

- `GET /health`
- `GET /answer?question_id=Q-001&question=질문내용`

현재 문서 검색기는 빈 로컬 검색기로 구성되어 있습니다. 이후 문서 분할, CLOVA Embedding, 벡터 DB, CLOVA 답변 생성을 단계적으로 연결합니다.

### CLOVA 문단 나누기 설정

`.env`에 `CLOVA_API_KEY`를 설정한 뒤 `ClovaClient.segment_text()`를 호출하면 최신 문단 나누기 API를 사용합니다. 기본값은 `alpha=-100`, `segCnt=-1`, `postProcess=true`이며, API 응답의 `topicSeg`를 임베딩 대상 문단 목록으로 변환합니다.

`ClovaClient.embed_text()`는 `POST /v1/api-tools/embedding/v2`를 호출합니다. 응답의 `embedding`은 1024차원 벡터이며, 다음 단계에서 이 벡터와 원문을 Vector DB에 저장합니다.

`ClovaClient.rag_reasoning()`은 `POST /v1/api-tools/rag-reasoning`을 호출합니다. 1차 응답에 `toolCalls`가 포함되면 서버가 검색 함수를 실행한 뒤, 해당 결과를 `tool` 메시지로 포함해 2차 호출해야 최종 답변을 받을 수 있습니다.

`ClovaClient.rerank_documents()`는 Vector DB에서 검색한 문서 목록을 `POST /v1/api-tools/reranker`로 보내고, 관련 문서·인용 문서·추천 검색어를 반환합니다.

### CLOVA client structure

CLOVA Studio API별 구현은 `rag/clients/`에 분리되어 있습니다. 공통 HTTP 인증은 `base.py`가 담당하고, 문단 나누기·임베딩·리랭커·RAG Reasoning은 각각의 client가 담당합니다. 기존 `ClovaClient` facade는 호환성을 위해 남아 있습니다.
