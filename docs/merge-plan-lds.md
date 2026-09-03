# 선별 병합 계획: `origin/lds` (친구) ↔ `refactor/centralized-db-config` (우리)

> 2026-09-03 작성. D-3 (제출 마감 09.06). 관련: [`deployment-design.md`](./deployment-design.md)

## 요약

- 공통 조상: `54f2611`
- 우리: 3커밋 / 36파일 (+3974/-65) — 대부분 Colab 빌드 파이프라인 + 문서 + WIP
- 친구(`lds`): **35커밋 / 90파일 (+4744/-1442)** — 서빙 리팩터 + NCP 배포 + stage1/3/4 개선 + 테스트 완비
- **확정 방향:** 임베딩 = `intfloat/multilingual-e5-large` (**non-instruct**) + fastembed/ONNX + `chromadb==1.5.9` PersistentClient. (사용자 확인 완료)

## 전략: **친구 `lds`를 통합 베이스로, 우리 고유분을 그 위에 replay**

이유:
- 친구 브랜치가 10배 분량 + 테스트 + NCP 배포 준비까지 완료. 이걸 베이스로 하는 게 손실 위험이 적다.
- 우리 고유 기여는 **격리가 잘 됨**(ingestion 파이프라인 + 문서) → 그 위에 얹기 쉽다.
- 친구의 서빙 경로(`LocalHybridRetriever` + `local_chroma`)는 우리 방향과 호환. 임베딩 어댑터만 교체하면 됨.

역방향(우리 베이스 + 친구 체리픽)은 공유 16파일에서 충돌 폭발 + 35커밋 중 누락 위험 → 비권장.

### 실행 방식

```bash
git checkout -b integration/merge-lds origin/lds        # 친구 브랜치에서 새 통합 브랜치
# 우리 고유분을 골라서 가져오기 (아래 표의 "우리→가져옴")
git checkout refactor/centralized-db-config -- <경로들>
# 임베딩 어댑터 교체 (아래 3장)
# 테스트 → 스모크 → PR to main
```

---

## 파일별 처리

### A. 친구 것 그대로 채택 (우리 방향과 무관한 개선)

| 영역 | 파일 | 내용 |
|---|---|---|
| stage1 | `pipeline/query_plan.py`(신규), `router.py`, `slot_extractor.py`, `calculation.py`, `entity_linker.py`, `build_intent.py`, `config/metric_router.json`, `models/intent.py`, `validator.py`, tests | 결정적 multi-query 계획, '최근 N년' 코퍼스 경계 파싱, 파생 비율 라우팅, compare_axis 분리 |
| stage3 | `grounding.py`(신규, +157), `node.py`(+186), `agents/{answer,event_linker,fact_extraction}.py`, `deterministic/calculations.py`, `parsing/structured.py`, `contracts.py`, tests | scoped fact grounding, 재무 테이블 근거 우선 파싱, 파생 비율/다기간 추세, linked event 게이팅 |
| stage4 | `node.py`(+111), `semantic.py`, `contracts.py`, `README.md`, `tests/test_stage4.py` | 근거/인용 처리 |
| integration | `rate_limit.py`(+95), `clova.py`(+58), `api.py`, `readiness.py`, `service.py`, `supervisor.py` | CLOVA 공유 rate-limit, bounded request budget, grounded fallback, provider tracing |
| ops | `scripts/smoke_api.py`(신규), `scripts/check_real_index.py`(신규), `.github/workflows/real-index.yml`, `pytest.ini`, `shared_state.py` | GET /answer 스모크, 실인덱스 검증 CI |
| state | `stage3` subquery result envelopes, query rollback/redacted trace | |

### B. 친구 것 채택하되 **임베딩만 우리 것으로 교체** (3장 참조)

| 파일 | 친구 버전 | 조치 |
|---|---|---|
| `stage2/local_store.py` | `LocalHybridRetriever` (read-only, `collection_name="chunk_vectors"`, `table_name="chunk_index"`, `readonly_sqlite_engine`) — Chroma 계속 사용 | **채택.** `from stage2.embedding import ClovaEmbeddings` 잔재만 정리 |
| `stage2/backends.py` | `local_chroma()`(존재-검증 강화), `readonly_sqlite_engine()`, `ReadOnlyHnswVectorStore`(hnswlib 폴백, 미사용) | **채택.** `ReadOnlyHnswVectorStore`는 폴백으로 보존(선택) — `hnswlib`만 의존, torch 아님 |
| `stage2/node.py`, `stage2/retrieval.py` | 읽기전용 백엔드 wiring | 채택 |
| `integration/composition.py` | `E5InstructEmbeddings(query_instruction=None)`, `if settings.embedding != "e5-instruct": raise` | 채택 후 **임베딩 팩토리/가드를 우리 값으로 수정** |
| `config.py` | `VALID_STAGE2_EMBEDDINGS=("e5-instruct",)`, `EMBEDDING` 기본 `e5-instruct`, `STAGE2_MODE`(local/container), `STAGE2_ALLOW_PARTIAL_INDEX`, `STAGE2_SQL_TABLE`, `STAGE2_CHROMA_COLLECTION` | 새 env 노브는 **채택**. 임베딩 enum만 `("e5",)` 또는 `("e5","e5-instruct")`로, 기본 `e5` |
| `requirements.txt` | `+torch==2.1.0`, `+sentence-transformers==3.4.1`, `+transformers==4.49.0`, `+huggingface-hub`, `+langchain-huggingface`, `+chroma-hnswlib==0.7.6` | **이 라인들 제외.** 우리 `fastembed` + `chromadb==1.5.9` 유지. `ReadOnlyHnswVectorStore` 보존 시 `hnswlib` 1줄만 추가 |
| `NCP_DEPLOYMENT.md`, `README.md`, `.env.example` | `STAGE2_EMBEDDING=e5-instruct`, instruct 모델 캐시 안내 | 구조는 채택, **임베딩 값·모델명을 non-instruct로 수정**. `STAGE2_MODE=local` 노브 반영 |

### C. 우리 것 유지 (친구 브랜치에 없음 → replay)

| 파일 | 내용 |
|---|---|
| `scripts/colab_build_chunk_index.py` (+1264) | Colab 인덱스 빌드 파이프라인 |
| `stage2/ingestion/` 전체 (`__init__.py`, `plain.py`, `dart/{__init__,chunker,parsers,rows,converters,embeddings}.py`, `dart/requirements.txt`) | DART 전처리·청킹·임베딩(fastembed e5-large) |
| `scripts/build_chunk_index.py` | 로컬 빌드 |
| `docs/{deployment-design,sync-chunk-index,colab_build,chunk_index,local_db}.md` | |
| `CLAUDE.md` (+182) | |
| `dart_agent_info.pdf` | 대회 자료 |
| `requirements.txt`의 `fastembed`, `chromadb==1.5.9` 핀 | |

> 주의: 친구가 `stage2/ingestion.py`를 **삭제**했고 우리는 `stage2/ingestion/plain.py`로 **이동**했다. → 우리 패키지 구조가 이김 (ingestion 측은 우리 담당).

### D. 양쪽 폐기 (fixture/smoke 인프라 — 둘 다 이미 이탈)

친구가 삭제한 것 그대로 삭제 유지:
`stage2/json_fixture.py`, `scripts/build_local_smoke_index.py`, `scripts/build_local_sqlite.py`, `scripts/build_subset_corpus.py`, `scripts/migrate_legacy_sqlite.py`, `data/local_smoke/selected_documents.json`, `legacy/test_data/disclosure_clova_local.json`, `tests/test_ingestion.py`, `tests/test_stage2_fixture.py`

### E. 선택 (친구의 A/B 하니스 — non-instruct에도 유용)

`stage2/query_instruction_eval.py`, `scripts/compare_query_instruction.py`, `tests/fixtures/e5_query_instruction_gold.json`, `tests/test_query_instruction_eval.py`
→ non-instruct e5-large에도 `query:` prefix 유무 A/B에 재활용 가능. 채택하되 **NCP 기본은 prefix 없음**(친구 결론과 동일: 안전성 게이트).

---

## 임베딩 어댑터 교체 (B의 핵심 작업)

친구 `stage2/embedding.py` = `E5InstructEmbeddings` (sentence-transformers, `e5-large-instruct`, `"Instruct: ...\nQuery:"`).
우리 `stage2/ingestion/dart/embeddings.py` = fastembed `intfloat/multilingual-e5-large`, 1024-dim, `query:`/`passage:` prefix 자동.

작업:
1. `stage2/embedding.py`를 우리 버전으로: `langchain_core.embeddings.Embeddings` 구현체를 fastembed로 래핑. `embed_query` → fastembed `query_embed`, `embed_documents` → `embed` (fastembed가 prefix 처리), 그 뒤 L2 정규화 (`l2_normalize` 재사용).
2. `integration/composition.py`:
   - `from stage2.embedding import E5InstructEmbeddings` → 우리 클래스명
   - `_build_embeddings`: `E5InstructEmbeddings(query_instruction=None)` → `E5FastEmbedEmbeddings()`
   - 가드 `if settings.embedding != "e5-instruct"` → `!= "e5"`
3. `config.py`: `VALID_STAGE2_EMBEDDINGS = ("e5",)`, `EMBEDDING` 기본 `"e5"`.
4. `requirements.txt`: torch/sentence-transformers/transformers/langchain-huggingface/chroma-hnswlib 제거 (C의 fastembed·chromadb 핀 유지). `ReadOnlyHnswVectorStore` 보존 시 `hnswlib` 추가.
5. `integration/readiness.py`: `validate_embedding_dimension` → 1024 확인 (e5-large/instruct 둘 다 1024이므로 통과. 모델 불일치는 차원으론 못 잡음 — 스모크로 검증).
6. 문서(`NCP_DEPLOYMENT.md`, `README.md`, `.env.example`): `STAGE2_EMBEDDING=e5`, 모델 = `intfloat/multilingual-e5-large`, fastembed 캐시 오프라인 배포 안내.

---

## 검증 (병합 후)

1. `pytest` 전체 — 친구 테스트 + 우리 테스트. instruct 전제 테스트(`tests/test_embedding.py` 등)는 non-instruct로 수정.
2. `python scripts/check_real_index.py --allow-partial-index` — 실 Chroma + `chunk_index.db`.
3. `python scripts/smoke_api.py` / `GET /answer?question_id=Q-TEST&question=...` — `STAGE2_EMBEDDING=e5`로 검색 결과에 실제 공시 청크가 실리는지.
4. 응답 5필드(`question_id, question, retrieved_context, think_trace, answer`) 계약 확인.

---

## 열린 항목 / 리스크

- **`chunk_index.db` 스키마 확인 필수** — 다운로드 완료 후 `PRAGMA integrity_check`, `.schema`. 친구 서빙 코드는 테이블명 `chunk_index`, 컬럼 `id/doc_id/chunk_id/text/source_path/metadata_json` + 스칼라 메타(`corp_name, sector, base_year, base_month, rcept_dt, rcept_no, is_correction, report_nm, basis, section_name` 등)를 기대. **우리 Colab ingestion(`stage2/ingestion/dart/rows.py`)이 쓰는 스키마와 일치하는지** 대조.
- 모델 불일치는 1024-dim 검사로 안 잡힘 → 스모크에서 검색 품질로만 확인.
- 친구의 `ReadOnlyHnswVectorStore` 주석("older Chroma persistence format ... client can migrate SQLite on open")은 친구가 `chromadb` 1.0.x에서 겪은 것으로 추정. 우리는 1.5.9에서 `PersistentClient` 정상 확인함. 폴백으로만 보존.
- 친구 브랜치 기준이므로 우리 `config.py` 중앙화 작업(`refactor/centralized-db-config`의 목적)이 친구 버전에 반영됐는지 확인 — 안 됐으면 그 부분도 replay.

---

## D-3 순서

1. `chunk_index.db` 다운로드 완료 → 스키마 대조 (위 리스크 1번)
2. `git checkout -b integration/merge-lds origin/lds`
3. C 항목 replay (`git checkout refactor/centralized-db-config -- <경로>`)
4. B 임베딩 교체 (위 6단계)
5. D 삭제 유지 확인, E 선택 반영
6. 검증 4단계
7. `Dockerfile` + `docker-compose.yml` 작성 (deployment-design 5장)
8. NCP 배포 → endpoint 확정 → 제출물 마무리 → `main` PR (09.06 전)

---

## 실행 결과 (2026-09-03, branch `integration/merge-lds`)

베이스 `origin/lds`(8f04040)에서 아래를 적용. **모듈성 우선 방침 반영** — 친구 코드가
좁힌 방향은 우리 쪽으로 되돌리되, 친구가 유지한 것(local/container, postgres/sqlite,
chroma persist/server)은 그대로 둠.

**스키마 대조:** `chunk_index.db` = 테이블 `chunk_index`, 컬럼 `id/doc_id/chunk_id/text/
source_path/metadata_json` + 프로모트 스칼라 12개(`corp_name…section_name`) + `raw_json_content`.
친구 서빙 코드 기대치와 **일치**. `_build_state` 부가 테이블 있음(무해).

**적용된 변경 (vs origin/lds):**
- `stage2/embedding.py` — `E5Embeddings`(fastembed, non-instruct) **추가**, 기본값.
  `E5InstructEmbeddings`/`format_e5_query`/`QUERY_INSTRUCTION`은 **삭제하지 않고 유지**
  (e5-instruct A/B 경로 보존).
- `integration/composition.py::_embedding_function` — `raise` 대신 **디스패치**
  (`e5`→fastembed, `e5-instruct`→sentence-transformers, else 목록과 함께 에러).
- `config.py` — `EMBEDDING` 기본 `e5`, `VALID_STAGE2_EMBEDDINGS=("e5","e5-instruct")`
  (단일값으로 축소 금지 주석 추가).
- `stage2/ingestion/writer.py` — **신규**. 빌드 시점 write 경로(DDL+upsert)를 read-only
  서빙 `LocalHybridRetriever`에서 분리. **엔진 무관** — SQLite/Postgres 둘 다 한 경로.
  `write_rows` / `write_rows_with_embeddings` / `ensure_schema` / `CHUNK_TABLE`.
- `scripts/build_chunk_index.py` — writer 모듈 + `stage2.backends` 사용으로 갱신.
  `--rdb-url` 추가(Postgres 타깃). clova 임베딩 인자 제거(어댑터 삭제됨).
- `requirements.txt` — 재구성. `chromadb==1.5.9` 명시 고정, `fastembed` 추가,
  `hnswlib` 추가(친구 `ReadOnlyHnswVectorStore` 서빙 경로가 실제로 사용 — `create_directory=False`),
  `chroma-hnswlib==0.7.6` 핀 제거(chromadb가 자체 해결). **`psycopg[binary]`/`pgvector`
  유지**(container 방향). torch/sentence-transformers/transformers는 **주석 처리된
  optional 섹션**으로 이동(e5-instruct 전용).
- `stage2/__init__.py` — `E5Embeddings`, `ChunkRow`, `PROMOTED_COLUMNS` export 추가.
- `tests/test_embedding.py` — e5 기본 디스패치/모델명 일치/정규화/미지원값 에러 테스트 추가.
- C 항목(Colab 빌드 파이프라인, `stage2/ingestion/**`, `docs/**`, `CLAUDE.md`) replay 완료.
- D 항목(fixture/smoke 인프라) — 친구 삭제 상태 유지.

**정적 검증 완료:** 변경 `.py` 전부 `py_compile` / `compileall` 통과. dangling 참조
스캔(`ClovaEmbeddings`, `json_fixture`, `local_store.write_rows` 등) — `legacy/` 외 없음.

**미검증 (풀 환경 필요 — 이 환경엔 langchain/fastapi/pytest/fastembed 없음):**
- `pytest` 전체 스위트
- `python scripts/check_real_index.py --allow-partial-index`
- `python scripts/smoke_api.py` / `GET /answer` (실 Chroma + sqlite, `STAGE2_EMBEDDING=e5`)
- `Dockerfile` / `docker-compose.yml` 아직 없음 (deployment-design 5장)

**주의:** 친구 서빙 기본 경로는 `ReadOnlyHnswVectorStore`(hnswlib 직접 read)다.
표준 Chroma `PersistentClient` 경로도 `chromadb==1.5.9`에서 동작 확인됨 —
`local_chroma(create_directory=True)`로 도달 가능. HNSW 리더에 문제 생기면 그쪽으로.
