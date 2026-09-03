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

`e5-instruct`(sentence-transformers, `intfloat/multilingual-e5-large-instruct`)는
instruction-prefix A/B 경로로 **선택 가능한 상태로 남겨둔다** — `STAGE2_EMBEDDING=e5-instruct`
+ `requirements.txt` 의 optional 섹션 설치. A/B 에서 검색은 개선됐으나 정보 한계 안전성
gate 가 실패했으므로 운영 기본값은 raw query 이며, 별도 승인 없이 prefix 를 적용하지 않는다.

```text
Instruct: Retrieve relevant passages from Korean corporate disclosure filings that directly answer the financial question.
Query: {question}
```

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

container 모드는 Postgres와 Chroma 서버가 실제로 준비된 별도 환경에서만 사용한다. 현재
제공 인덱스 검증과 서버 테스트의 기준은 `STAGE2_MODE=local`이다.

일반 회귀 테스트는 대용량 인덱스를 필요로 하지 않고 `pytest`에서 `real_index` marker를
제외한다. 실제 인덱스가 사전 탑재된 runner에서는 수동으로 다음 명령을 실행한다.

```powershell
pytest -m real_index
python scripts/check_real_index.py --allow-partial-index
python scripts/compare_query_instruction.py
```

`compare_query_instruction.py`는 사전 탑재된 실제 DB와 E5 cache에서만 실행하는 수동
A/B 검증이다. 검색 가능 20개는 유형별 Recall@20 무회귀와 전체 5%p 이상 개선을 동시에
요구하고, 정보 한계 5개는 근거 없는 답변·숫자 생성을 검사한다. 조건을 충족하지 않으면
운영 prefix를 자동으로 바꾸지 않고 `KEEP_RAW` 결과를 기록한다.

NCP live smoke는 별도 터미널에서 worker 1개로 서버를 실행한 뒤
`python scripts/smoke_api.py --base-url http://127.0.0.1:8000`을 사용한다. 이 CLI는
`/health` → `/ready` → `/answer` 순서와 5개 문자열 필드만 확인하고 API key·authorization
header·응답 전문을 출력하지 않는다.
