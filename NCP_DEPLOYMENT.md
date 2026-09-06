# NCP 배포 (환경·검증 레퍼런스)

> 서버 생성부터 컨테이너 실행까지 단계별 안내는 **[`docs/ncp-deploy.md`](docs/ncp-deploy.md)** 를 본다.
> 이 문서는 환경변수·검증 명령의 레퍼런스다.

## 인덱스 마운트

서버의 볼륨(예: `/data/local_db/`)에 제공 인덱스를 배치하고, 컨테이너에는
`/app/data/local_db` 로 read-only 마운트한다 (`docker-compose.yml`).

```text
/data/local_db/chunk_index.db
/data/local_db/chunk_index_chroma/
```

압축본을 사용하는 경우 먼저 SHA-256 체크섬을 검증한 뒤 해제한다. 실행 중 인덱스를
재생성하거나 변환하지 않는다. SQLite와 Chroma는 서비스에서 read-only로 연다.

## 서버 환경

`docker-compose.yml` 이 컨테이너 기준 경로·모드를 이미 주입하므로, 서버의 `.env` 에는
보통 CLOVA 값만 채우면 된다. 전체 목록:

```env
# .env (서버에만, 커밋 금지)
CLOVA_API_KEY=<secret>
CLOVA_API_HOST=clovastudio.stream.ntruss.com
CLOVA_LLM_ENABLED=true
CLOVA_CHAT_MODEL=HCX-DASH-002
STAGE1_USE_LLM=0
CLOVA_RERANKER_ENABLED=false
CLOVA_RERANKER_CANDIDATE_LIMIT=100

# compose 가 아래를 이미 넣지만, 직접 uvicorn 실행 시엔 명시한다
STAGE2_MODE=local
STAGE2_EMBEDDING=e5
STAGE2_INDEX_PATH=/app/data/local_db/chunk_index.db
STAGE2_CHROMA_PATH=/app/data/local_db/chunk_index_chroma
STAGE2_CHROMA_COLLECTION=chunk_vectors
STAGE2_SQL_TABLE=chunk_index
STAGE2_ALLOW_PARTIAL_INDEX=true
# FASTEMBED_CACHE_DIR=/opt/models/fastembed   # 이미지에 내장됨
```

`STAGE2_ALLOW_PARTIAL_INDEX=true`는 공유 인덱스의 예상된 ID·manifest 불일치만 경고로
낮춘다. 스키마·빈 테이블·컬렉션·차원 오류는 기동을 막는다. Secret은 `.env`, 로그,
Git, README, 응답에 기록하지 않는다.

### 임베딩

### Process-local cache

앱은 pipeline 생성마다 독립적인 bounded TTL/LRU cache를 만든다. 기본 설정은 다음과 같다.

```env
DIS164_CACHE_ENABLED=true
DIS164_CACHE_TTL_SECONDS=600
DIS164_CACHE_INDEX_VERSION=1
DIS164_CACHE_QUERY_EMBEDDING_MAX=256
DIS164_CACHE_STRUCTURED_DOC_MAX=512
DIS164_CACHE_FACT_MAX=1024
DIS164_CACHE_CANDIDATE_MAX=16
```

E5 질의 임베딩, manifest filter SQL 후보, structured parsing, intent별 Fact extraction만
재사용하고 CLOVA Chat/Reranker 응답은 재사용하지 않는다. cache는 SQLite·Chroma를 변경하지
않으며, signature·version·TTL로 무효화된다. cache 내부 오류는 원래 계산으로 우회한다.
worker는 독립 cache를 가지므로 현재 권장하는 one-worker 운용과 호환된다.

기본값은 **`STAGE2_EMBEDDING=e5`** — 인덱스를 만든 모델과 동일한
`intfloat/multilingual-e5-large` (1024-dim, **non-instruct**)를 **fastembed / ONNX**로 로드한다.
가중치는 **컨테이너 이미지에 빌드 시 내장**되므로 기동 시 외부 다운로드가 없다
(`Dockerfile` 의 builder 단계, `FASTEMBED_CACHE_DIR=/opt/models/fastembed`).
torch / transformers / sentence-transformers 는 필요 없다.

`STAGE2_MODE=local` 의 벡터 검색은 `backends.ReadOnlyHnswVectorStore` 가 담당한다 —
공급 Chroma persist 디렉터리의 `chroma.sqlite3` 를 `mode=ro` 로 읽고 HNSW 파일을 직접
연다 (Chroma writer/client 를 열지 않아 마이그레이션이 없다). 표준 Chroma
`PersistentClient` 경로도 `chromadb==1.5.9` 에서 동작 확인됨 —
`backends.local_chroma(create_directory=True)` 로 도달 가능하며, HNSW 리더에 문제가
생기면 그쪽으로 전환한다.

`e5-instruct`는 active 경로에서 제거했다. 기존 공급 인덱스와 같은
`intfloat/multilingual-e5-large`를 문서·질의에 공통으로 사용하며, Docker runtime은
사전 캐시된 모델만 사용한다.

Reranker는 `CLOVA_RERANKER_ENABLED=true`일 때만 production pipeline에 연결한다.
상위 100개 후보를 전송하고 장애 시 deterministic hybrid 결과로 fallback한다.

## 실행과 확인

권장은 컨테이너 (`docs/ncp-deploy.md` 6단계):

```bash
INDEX_DIR=/data/local_db docker compose up -d --build
docker compose exec app python scripts/check_deployment.py
docker compose exec app python scripts/smoke_api.py --base-url http://127.0.0.1:8000
```

컨테이너 없이 직접 실행할 때:

```bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-langgraph.txt
python -m pip install -r requirements-dev.txt
python scripts/check_deployment.py
python scripts/check_real_index.py --allow-partial-index
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

외부에서 `/health`, `/ready`, `/answer`를 순서대로 호출한다. `/answer`는
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer` 다섯 필드를
문자열로 반환해야 한다. local Chroma와 process-local rate limiter를 사용하므로 worker는
1개로 유지한다.

Retriever는 `STAGE2_MODE=local`만 지원한다. PostgreSQL container와 원격 Chroma 경로는
제거했으며, `STAGE2_MODE=container` 같은 잘못된 설정은 자동 전환 없이 readiness 오류로
실패한다. 현재 제공 인덱스 검증과 서버 테스트의 기준은 local SQLite·Chroma다.

일반 회귀 테스트는 대용량 인덱스를 필요로 하지 않고 `pytest`에서 `real_index` marker를
제외한다. 실제 인덱스가 사전 탑재된 runner에서는 수동으로 다음 명령을 실행한다.

```powershell
pytest -m real_index
python scripts/check_real_index.py --allow-partial-index
```

검색 backend 대체 실험은 production과 분리된 `docs/retrieval-experiments.md` sidecar에서
수행한다. Production Reranker의 ON/OFF 비교는 별도 smoke에서 확인한다.

NCP live smoke는 별도 터미널에서 worker 1개로 서버를 실행한 뒤
`python scripts/smoke_api.py --base-url http://127.0.0.1:8000`을 사용한다. 이 CLI는
`/health` → `/ready` → `/answer` 순서와 5개 문자열 필드만 확인하고 API key·authorization
header·응답 전문을 출력하지 않는다. 답변 불가·근거 부족 응답은 기존 결론 뒤에
결정론적인 이유와 필요한 경우 재질문 안내를 붙이며, 내부 `failure_reason_code`는
`think_trace`에만 기록한다. 공개 답변에는 API key·원시 provider 오류·검색 점수를
포함하지 않는다.
