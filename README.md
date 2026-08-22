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

### Corpus inspection

승인된 공시 corpus만 검사하려면 다음 명령을 사용합니다.

```powershell
python -m scripts.inspect_corpus --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시" --limit 10
```

### CLOVA 문단 나누기 설정

`.env`에 `CLOVA_API_KEY`를 설정한 뒤 `ClovaClient.segment_text()`를 호출하면 최신 문단 나누기 API를 사용합니다. 기본값은 `alpha=-100`, `segCnt=-1`, `postProcess=true`이며, API 응답의 `topicSeg`를 임베딩 대상 문단 목록으로 변환합니다.

`ClovaClient.embed_text()`는 `POST /v1/api-tools/embedding/v2`를 호출합니다. 응답의 `embedding`은 1024차원 벡터이며, 다음 단계에서 이 벡터와 원문을 Vector DB에 저장합니다.

`ClovaClient.rag_reasoning()`은 `POST /v1/api-tools/rag-reasoning`을 호출합니다. 1차 응답에 `toolCalls`가 포함되면 서버가 검색 함수를 실행한 뒤, 해당 결과를 `tool` 메시지로 포함해 2차 호출해야 최종 답변을 받을 수 있습니다.

`ClovaClient.rerank_documents()`는 Vector DB에서 검색한 문서 목록을 `POST /v1/api-tools/reranker`로 보내고, 관련 문서·인용 문서·추천 검색어를 반환합니다.

### Build the vector index

실제 색인은 CLOVA API 키와 PostgreSQL 접속정보가 설정된 환경에서 실행합니다.

```powershell
python -m scripts.build_index --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시" --limit 10
```

`--limit`을 생략하면 manifest 전체를 처리합니다. 동일한 원본 해시와 chunk가 이미 있으면 문서를 건너뛰므로 중단 후 재실행할 수 있습니다.

### Retrieval structure

`rag/retrieval/vector_search.py`는 질문을 Embedding v2로 변환하고 PostgreSQL pgvector에서 cosine 검색을 수행합니다. `rag/retrieval/rerank.py`는 검색된 상위 chunk를 CLOVA 리랭커에 전달하고 `citedDocuments`를 원래 chunk 메타데이터와 연결합니다. 실제 DB와 CLOVA API가 준비되기 전에는 두 구성요소에 fake 구현을 주입해 로컬 테스트할 수 있습니다.

DB 없이 로컬 검색 흐름을 확인하려면 다음 명령을 실행합니다.

```powershell
python -m scripts.local_smoke_test
```

현재 로컬 smoke test는 추가 패키지 설치 없이 동작하는 임시 cosine store와 deterministic fake embedding을 사용합니다. FAISS 또는 Chroma는 실제 로컬 색인 규모가 필요할 때 교체 도입합니다.

### CLOVA client structure

CLOVA Studio API별 구현은 `rag/clients/`에 분리되어 있습니다. 공통 HTTP 인증은 `base.py`가 담당하고, 문단 나누기·임베딩·리랭커·RAG Reasoning은 각각의 client가 담당합니다. 기존 `ClovaClient` facade는 호환성을 위해 남아 있습니다.
