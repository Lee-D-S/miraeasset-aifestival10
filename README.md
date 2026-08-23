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

실제 공시 문서 일부를 로컬 색인하려면 다음 명령을 사용합니다. 기본값은 manifest의 첫 5개 문서이며, 결과는 Git에서 제외되는 `test_data/`에 저장됩니다.

```powershell
python -m scripts.build_local_index --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시" --limit 5
```

CLOVA 문단 나누기 API를 문서 1개로 테스트하려면 다음 명령을 사용합니다. 이 명령부터 CLOVA API 사용량이 발생합니다.

```powershell
python -m tests.test_clova_segmentation --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시"
```

문서가 API 입력 한도를 넘으면 segmentation client가 줄바꿈 경계를 우선해 여러 요청으로 나누어 처리합니다. 단독 테스트는 기본적으로 문서 앞부분 20,000자만 호출하며, `--max-chars`로 조정할 수 있습니다.

문단 나누기 후 첫 문단 1개를 실제 Embedding v2로 변환하려면 다음 명령을 사용합니다. 문단 나누기와 임베딩 API 사용량이 각각 발생합니다.

```powershell
python -m tests.test_clova_embedding --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시" --max-chars 20000 --paragraph-index 0
```

정상 결과는 `embedding_dimension: 1024`를 포함해야 합니다. 이 smoke test는 전체 문서를 색인하지 않고 문단 1개만 호출합니다.

문단 나누기 결과와 Embedding v2 결과를 로컬 Vector Store에 저장하려면 다음 명령을 사용합니다. 기본값은 첫 문서의 최대 3개 문단이며, 이미 저장된 문단은 API를 다시 호출하지 않습니다.

```powershell
python -m scripts.build_clova_local_index --source "C:\Users\idong\OneDrive\바탕 화면\공모전\2026 미래에셋 ai 페스티벌\data\3.공시"
```

생성 파일:

- `test_data/segmentation_cache.json`: 문단 나누기 결과 캐시
- `test_data/disclosure_clova_local.json`: 문단 원문·출처·Embedding v2 벡터

전체 문단을 처리하려면 `--max-chunks -1`을 사용합니다. 이 경우 문단 수만큼 Embedding v2 API가 호출됩니다.

표지·목차 이후의 본문 문단부터 추가하려면 `--chunk-start`를 지정합니다. 예를 들어 `--chunk-start 4 --max-chunks 6`은 5번째 문단부터 6개를 색인합니다.

저장된 로컬 색인을 질문으로 검색하려면 다음과 같이 실행합니다. 이때 문서 임베딩은 재사용하고 질문 임베딩만 새로 생성합니다.

```powershell
python -m scripts.search_clova_local_index "2023년 1분기 사업 내용은 무엇인가요?" --top-k 3
```

검색 결과는 기본적으로 CLOVA 리랭커에도 전달됩니다. 리랭커 결과에는 `result`, `suggested_queries`, `cited_documents`가 포함됩니다. `--rerank-top-k`로 리랭커에 전달할 문서 수를 조정할 수 있습니다.

리랭커와 RAG Reasoning까지 연결한 로컬 end-to-end 테스트는 다음과 같이 실행합니다.

```powershell
python -m scripts.run_clova_local_rag "삼성전자의 2023년 1분기 공시 문서에 기재된 주요 사업 내용은 무엇인가요?" --retrieval-top-k 5 --rerank-top-k 5
```

이 명령은 검색 도구 호출, 로컬 검색, 리랭킹, 최종 답변 생성, 인용 문서 출력을 순서대로 수행합니다.

검색어에 기업명·분기·공시 유형이 명확히 포함되면 검색 tool 내부에서 메타데이터 필터를 자동 적용합니다. 예를 들어 `삼성전자 2023년 1분기 분기보고서`는 삼성전자·`2023-03`·분기보고서 조건으로 검색 범위를 제한합니다. 조건이 불명확하면 필터 없이 벡터 검색합니다.

대회용 `/answer`도 CLOVA API 키와 로컬 색인이 있으면 같은 로컬 RAG 흐름을 사용합니다. PostgreSQL DSN이 설정되면 PostgreSQL이 우선됩니다.

```text
GET /answer?question_id=Q-001&question=삼성전자의%202023년%201분기%20사업%20내용은%20무엇인가요?
```

`/answer`는 검색 결과가 없거나 리랭커가 관련 문서를 선택하지 못하면 근거 없는 답변을 생성하지 않고, `retrieved_context`를 비우고 다음 문구를 반환합니다: `제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.`

### CLOVA client structure

CLOVA Studio API별 구현은 `rag/clients/`에 분리되어 있습니다. 공통 HTTP 인증은 `base.py`가 담당하고, 문단 나누기·임베딩·리랭커·RAG Reasoning은 각각의 client가 담당합니다. 기존 `ClovaClient` facade는 호환성을 위해 남아 있습니다.
