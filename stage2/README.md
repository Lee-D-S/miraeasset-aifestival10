# Stage2

Stage2는 Stage1 Intent를 받아 공시 근거 문서를 검색하고 Stage3에 넘기는 검색 전용 모듈이다.
`manifest_filter.exclude_corp_names`가 있으면 기업 목록·섹터 조건보다 우선해 해당 기업의
문서를 제외한다.

## 백엔드

실행 factory는 `STAGE2_MODE`로 백엔드를 선택한다.

```text
local       read-only SQLite chunk_index + supplied Chroma persistent HNSW files
container   Postgres + Chroma 서버
```

기본 모드는 `local`이다. local 모드의 기본 인덱스는 다음과 같다.

```text
data/team-feature2-local-db/local_db/chunk_index.db
data/team-feature2-local-db/local_db/chunk_index_chroma/
```

제공 인덱스는 SQLite `chunk_index` 테이블 약 1,155,170행과 Chroma `chunk_vectors`
컬렉션 약 800,460개 벡터로 구성되어 있다. local 실행은 이 파일을 읽기만 하며 테이블을
생성하거나 행·벡터를 upsert하지 않는다.

## local 설정

```env
STAGE2_MODE=local
STAGE2_INDEX_PATH=data/team-feature2-local-db/local_db/chunk_index.db
STAGE2_CHROMA_PATH=data/team-feature2-local-db/local_db/chunk_index_chroma
STAGE2_CHROMA_COLLECTION=chunk_vectors
STAGE2_SQL_TABLE=chunk_index
STAGE2_EMBEDDING=e5
STAGE2_ALLOW_PARTIAL_INDEX=true
```

`e5`는 `intfloat/multilingual-e5-large` 하나만 허용한다. 문서와 질의는 저장 인덱스와
동일한 1024차원 E5 공간을 사용하며 `e5-instruct` 입력은 설정 validation 오류로 거부한다.
모델은 서버에 미리 캐시되어 있어야 하고 Docker runtime은 `HF_HUB_OFFLINE=1`로 외부
다운로드를 차단한다.

Production Reranker는 다음 독립 설정으로만 켠다.

```env
CLOVA_RERANKER_ENABLED=false
CLOVA_RERANKER_CANDIDATE_LIMIT=100
STAGE1_USE_LLM=0
```

Stage1은 별도의 `CORPUS_DIR`에서 `universe.csv`와 `manifest.jsonl`을 읽는다. 제공 인덱스는
벡터·문서 집합이 코퍼스와 완전히 일치하지 않을 수 있으므로 `STAGE2_ALLOW_PARTIAL_INDEX`
가 명시된 경우에만 해당 불일치를 경고로 낮춘다. 스키마 누락, 빈 테이블, Chroma 컬렉션
부재·차원 오류는 항상 실패한다.

## 실행 및 확인

```powershell
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
python -m pip install -r requirements-dev.txt
python scripts/check_deployment.py
python scripts/check_real_index.py --allow-partial-index
pytest -m real_index
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

외부에서 `/health` → `/ready` → `/answer` 순서로 확인한다. `/answer`는
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer`를 모두 문자열로
반환한다.

일반 CI에서는 InMemoryRetriever 기반 결정론적 회귀와 임시 SQLite/Chroma 계약 테스트를
사용한다. 실제 제공 DB 검증은 `real_index` marker와 별도 CLI로만 실행하며, DB와 E5 cache가
없는 환경에서 `pytest -m real_index`를 실행하면 실패한다.
HNSW 대체 후보(FTS5, Exact, FAISS IVFFlat/SQ8/PQ)는 production 설정과 분리된
실험용 sidecar·서비스로 제공한다. 생성과 실행 방법은
[docs/retrieval-experiments.md](../docs/retrieval-experiments.md)를 참고한다.
후보 벡터 검색은 production과 동일한 multilingual-e5-large와 raw query
계약을 사용하며, 별도의 instruct 임베딩을 사용하지 않는다.

## 안전 경계

## Process-local cache

canonical pipeline은 pipeline 생성 시 `integration.cache.CacheRegistry`를 하나 만들고
Stage2·Stage3에 공유한다. 기존 SQLite·Chroma 인덱스와 E5 모델 cache는 source of truth이며,
새 cache는 메모리에서만 동작하는 bounded TTL/LRU 최적화 계층이다.

```env
DIS164_CACHE_ENABLED=true
DIS164_CACHE_TTL_SECONDS=600
DIS164_CACHE_INDEX_VERSION=1
DIS164_CACHE_QUERY_EMBEDDING_MAX=256
DIS164_CACHE_STRUCTURED_DOC_MAX=512
DIS164_CACHE_FACT_MAX=1024
DIS164_CACHE_CANDIDATE_MAX=16
```

최종 검색 질의의 E5 query embedding, 동일 `manifest_filter`의 SQL 후보 본문, 문서별
structured parsing, intent profile별 Fact extraction만 cache한다. keyword/vector/hybrid
재정렬과 CLOVA Reranker·Chat 응답은 cache하지 않는다. index metadata·명시적 version·TTL로
무효화하며, cache 오류는 원래 계산으로 우회한다. cache 정보는 answer나 trace에 기록하지
않고, worker마다 독립적으로 유지한다.

- 제공 SQLite·Chroma 인덱스는 read-only로 연다.
- 제공 Chroma가 legacy persistent HNSW 형식이므로 local 실행은 Chroma writer/migration을
  열지 않고 SQLite `mode=ro`와 direct HNSW query adapter를 사용한다.
- `chunk_id`가 SQLite와 Chroma에서 일치하는지 readiness에서 확인한다.
- 부분 인덱스 경고는 명시적 플래그가 있을 때만 허용한다.
- Reranker는 기본 OFF이며 켜면 상위 100개만 전송하고 실패 시 deterministic rank로
  fallback한다. `suggestedQueries`는 자동 재검색하지 않고 trace에만 기록한다.
- OpenDART·뉴스·리포트 등 외부 데이터는 Stage2에서 사용하지 않는다.
- API key와 요청 원문은 로그·응답의 trace에 기록하지 않는다.
