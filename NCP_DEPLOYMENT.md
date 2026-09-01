# NCP 1차 배포

## 인덱스 준비

현재 브랜치의 Stage2는 SQLite metadata/chunk와 Chroma vector collection을 함께 사용한다.
구 브랜치 SQLite에 `embedding_json`이 있으면 embedding provider를 재호출하지 않고 다음처럼 이관한다.

```powershell
python scripts/migrate_legacy_sqlite.py `
  --input data/local_smoke/smoke.db `
  --output data/local_smoke/refactor_smoke.db `
  --chroma-dir data/local_smoke/refactor_smoke_chroma
```

선정 subset의 Stage1 metadata는 다음처럼 생성한다.

```powershell
python scripts/build_subset_corpus.py `
  --corpus <source-corpus> `
  --selection data/local_smoke/selected_documents.json `
  --output <subset-corpus>
```

`<subset-corpus>`에는 전체 기업 마스터 `universe.csv`와 선택 문서만 담긴 `manifest.jsonl`이
생성된다. Stage1의 alias 설정이 전체 universe를 참조할 수 있으므로 universe를 축소하지 않는다.
manifest의 문서 범위는 Stage2 SQLite index에 포함된 문서와 일치해야 한다.

## 서버 환경

```text
CORPUS_DIR=/app/data/corpus
STAGE2_MODE=local
STAGE2_INDEX_PATH=/app/data/index/refactor_smoke.db
STAGE2_CHROMA_PATH=/app/data/index/refactor_smoke_chroma
STAGE2_CHROMA_COLLECTION=stage2_chunks
CLOVA_LLM_ENABLED=true
CLOVA_API_KEY=<secret>
CLOVA_API_HOST=clovastudio.stream.ntruss.com
# 선택: Chat·Embedding 공유 admission budget
CLOVA_RATE_LIMIT_QPM=60
CLOVA_RATE_LIMIT_TPM=40000
CLOVA_CHAT_MIN_INTERVAL=0.2
```

컨테이너 RDB·Chroma 서버로 올릴 때는 `STAGE2_MODE=container`, `STAGE2_RDB_URL`,
`STAGE2_CHROMA_HOST`(`STAGE2_CHROMA_PORT`)를 설정한다. 경로는 모두 `config.py`가 관리하며
상대값은 프로젝트 루트 기준으로 해석된다.

Secret은 `.env`, 로그, Git, README, 응답에 기록하지 않는다.

## 실행과 확인

```bash
python scripts/check_deployment.py
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

외부에서 순서대로 `/health`, `/ready`, `/answer`를 호출한다. `/answer`는
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer`를 모두 문자열로 반환해야 한다.
provider rate-limit header와 로컬 admission 차단 상태는 응답의 `think_trace`에
`provider_status`로 구조화되어 기록된다. API key와 요청 본문은 응답이나 로그에 기록하지 않는다.
