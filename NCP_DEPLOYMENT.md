# NCP 배포

## 인덱스 마운트

서버의 `data/` 아래에 제공 인덱스를 배치한다.

```text
data/team-feature2-local-db/local_db/chunk_index.db
data/team-feature2-local-db/local_db/chunk_index_chroma/
```

압축본을 사용하는 경우 먼저 SHA-256 체크섬을 검증한 뒤 해제한다. 실행 중 인덱스를
재생성하거나 변환하지 않는다. SQLite와 Chroma는 서비스에서 read-only로 연다.

## 서버 환경

```env
CORPUS_DIR=/app/data/corpus
STAGE2_MODE=local
STAGE2_INDEX_PATH=/app/data/team-feature2-local-db/local_db/chunk_index.db
STAGE2_CHROMA_PATH=/app/data/team-feature2-local-db/local_db/chunk_index_chroma
STAGE2_CHROMA_COLLECTION=chunk_vectors
STAGE2_SQL_TABLE=chunk_index
STAGE2_EMBEDDING=e5-instruct
STAGE2_ALLOW_PARTIAL_INDEX=true
CLOVA_LLM_ENABLED=true
CLOVA_API_KEY=<secret>
CLOVA_API_HOST=clovastudio.stream.ntruss.com
```

`STAGE2_ALLOW_PARTIAL_INDEX=true`는 공유 인덱스의 예상된 ID·manifest 불일치만 경고로
낮춘다. 스키마·빈 테이블·컬렉션·차원 오류는 기동을 막는다. Secret은 `.env`, 로그,
Git, README, 응답에 기록하지 않는다.

`STAGE2_EMBEDDING=e5-instruct`를 사용하는 서버에는
`intfloat/multilingual-e5-large-instruct` 모델을 Hugging Face 캐시에 배포 전에 준비한다.
애플리케이션은 시작 시 모델을 외부에서 다운로드하지 않는다. 제공 Chroma 인덱스는
현재 Chroma writer와 다른 persistent HNSW pickle 형식이므로 local backend는 Chroma
migration/client 대신 SQLite `mode=ro`와 direct HNSW query adapter를 사용한다.
A/B candidate 질의 임베딩은 다음 고정 형식을 `embed_query()`에만 적용한다. 저장 문서
벡터는 raw text 계약을 유지한다. 실제 A/B에서 검색 성능은 개선됐지만 정보 한계 안전성
gate가 실패했으므로 현재 NCP production 기본값은 raw query이며, 별도 승인 없이 prefix를
운영에 적용하지 않는다.

```text
Instruct: Retrieve relevant passages from Korean corporate disclosure filings that directly answer the financial question.
Query: {question}
```

## 실행과 확인

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
