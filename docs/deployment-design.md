# 배포 설계·계획 문서 (team-feature2 → NCP)

> 상태: **작성 중 / 살아있는 문서.** Claude와 사용자가 같이 참조·갱신한다.
> 최초 2026-09-03. 관련: [`sync-chunk-index.md`](./sync-chunk-index.md), [`merge-plan-lds.md`](./merge-plan-lds.md), [`ncp-deploy.md`](./ncp-deploy.md)
> 결정에는 **[결정] / [잠정] / [미정]** 태그. 바뀌면 이 문서를 고친다.

---

## 진행 현황 (2026-09-03 세션 종료 시점)

**완료:**
- `origin/lds`(친구, 35커밋) ↔ `refactor/centralized-db-config`(우리) **선별 병합** → `main` 에 반영 (PR #4). 상세: `merge-plan-lds.md`.
  - 임베딩 = `e5` (fastembed / non-instruct) 단일 운영값. `e5-instruct`는 active 경로에서 제거하고 디스패치는 `integration/composition._embedding_function`이 담당.
  - write 경로는 `retriever/ingestion/writer.py` 로 분리해 로컬 SQLite 인덱스를 만든다. 서빙 retriever는 read-only로 유지한다.
  - `chromadb==1.5.9` 고정, `fastembed`/`hnswlib` 추가. active Retriever는 로컬 SQLite·Chroma만 지원한다.
- `chunk_index.db` 스키마 확인 — 서빙 코드 기대치와 일치 (`chunk_index` 테이블, 필수 6컬럼 + 스칼라 메타 + `raw_json_content`).
- **컨테이너화 완료:** `Dockerfile`(멀티스테이지, e5 ONNX 내장, non-root), `.dockerignore`, `docker-compose.yml`(local 단일 구성).
- **CI:** 대회 측 제약(GitHub Actions 사용 금지)에 따라 `.github/workflows/` 워크플로우는 제거함. 로컬 검증 명령은 `pytest.ini` 기준으로 수행.
- **NCP 배포 가이드:** `docs/ncp-deploy.md` (단계별), `NCP_DEPLOYMENT.md`(환경 레퍼런스, e5 로 갱신).
- 브랜치 운영: `main` 은 PR 로만 갱신하기로 합의 (충돌 재발 방지). 워크플로 파일은 사용자가 전담.

**아직 안 됨 (D-3):**
- **풀 환경 `pytest` 통과 확인** — 이 세션 환경엔 deps 없어 정적 검증만 함. CI(`913edfe` 이후)에서 초록인지 확인 필요.
- **실 인덱스 스모크** — `scripts/check_real_index.py`, `GET /answer` (`RETRIEVER_EMBEDDING=e5`, 실 Chroma+sqlite).
- **NCP 서버 실제 배포** — `docs/ncp-deploy.md` 따라. endpoint URL 확정.
- **제출물** — README 에 실행법·API 명세(요청/응답 5필드) + endpoint URL, 기술제안서.

---

## 0. 일정 — D-3 (마감 임박)

| 항목 | 일자 | 메모 |
|---|---|---|
| **예선 제출 마감** | **09.06** | 소스+Dockerfile+requirements+README+기술제안서+API 정보. 이후 커밋-push·서버 배포 변경 시 **실격** |
| 예선 평가 | 09.07 ~ 09.30 (자료 p3) / 09.07~09.20 (p8, 변경 시 공지) | 이 기간 **API 상시 활성 유지 필수** |
| 결과 발표 | 10.01 | |

> 제출물은 주최 측 GitHub Org의 Private Repo에 push. 대용량은 클라우드 스토리지 링크로.
> NCP 크레딧 **한도 초과분은 자기 부담** — 09.07~30 상시 가동 비용 주의.

---

## 1. 과제 / 하드 제약 (평가 직결 — 반드시 준수)

**주제:** 공시(disclosure) AI Agent. 공시 데이터 기반으로 검색·비교·연산·근거기반 답변.
대상 기간 2023.01 ~ 2026 Q1. 데이터는 주최 측 제공 코퍼스(공시 원문 + 구조화 데이터)만.

| 제약 | 내용 |
|---|---|
| **LLM** | **HyperCLOVA X 만 허용.** 다른 LLM 사용 시 평가 제외. (코드: `integration/clova.py`, `langchain_naver`) |
| 데이터 | 제공 코퍼스 외 사용 불가 (뉴스·리포트·위키 등). OpenDART 등 외부 공시 API 실시간 호출 불가 |
| 근거 | 모든 답변에 근거 공시 표시. 확인 불가 시 "확인할 수 없음 / 공시에서 확인되지 않음" 명시 |
| 금지 | 공시에 근거 없는 미래 예측·투자 의견 생성 |

---

## 2. 평가용 API 계약 [결정 — 주최 측 스펙]

```
GET /answer?question_id={id}&question={평가 질의}
```

응답 (JSON):

```json
{
  "question_id": "Q-001",
  "question": "평가 질의 원문",
  "retrieved_context": "답변 생성에 참고한 검색 문서",
  "think_trace": "사고·추론·도구 사용 과정",
  "answer": "최종 생성 답변"
}
```

- 메서드 **GET**, 쿼리 파라미터 2개. 응답 5필드 고정.
- `retrieved_context` = 검색 근거(공시명·공시일 포함), `think_trace` = 추론 과정.
- 답변 불가·근거 부족 시 `answer`는 기존 결론 문장 뒤에 결정론적인 이유와 필요한
  경우 재질문 안내를 포함한다. 내부 `failure_reason_code`는 `think_trace`에만 기록한다.
- 코드: `reasoner/api_contract.py`, `reasoner/api.py`, `integration/api.py` 에 계약 구현.
- **미정:** 최종 endpoint URL (NCP 배포 후 확정 → 제출 API 명세서에 기재).

---

## 3. 실제 스택 (코드 기준)

| 레이어 | 구현 | 비고 |
|---|---|---|
| API | FastAPI + `uvicorn[standard]` | `app.py` |
| 설정 | `pydantic-settings` + `.env` (`config.py`) | 아래 4장 환경변수 |
| 에이전트 | LangGraph 1.0 (`integration/graph.py`, `supervisor.py`), interpreter~4 파이프라인 | |
| 벡터 DB | **Chroma `PersistentClient`, `chromadb==1.5.9`** | `data/local_db/chunk_index_chroma/`, 컬렉션 `chunk_vectors` 5,548,951 rows, ~95 GB |
| 관계형 DB | **SQLite `data/local_db/chunk_index.db`** (`sqlalchemy`) | ~2026-09-03 시점 **다운로드 중**. 스키마·무결성 미확인. `sqlite_master` 헤더상 500만+ 페이지(≈20 GB대) |
| 임베딩 | **`intfloat/multilingual-e5-large`** (1024-dim), **fastembed / ONNX Runtime** | `retriever/ingestion/dart/embeddings.py`. torch/transformers/sentence-transformers **미사용**(의도). not-instruct 고정 |
| LLM | **HyperCLOVA X** — `integration/clova.py` (HTTP, `clovastudio.stream.ntruss.com`, 예: `HCX-DASH-002`) + `langchain_naver` | |

### 데이터 자산

- **Chroma** `chunk_index_chroma/` = `chroma.sqlite3`(~78 GB) + HNSW 세그먼트 `de28b1f0-...`(~22 GB). Colab 빌드 → 수동 동기화. 상세: sync 런북.
- **`chunk_index.db`** = 청크 원문/공시 메타 관계형 인덱스로 추정. 다운로드 완료 후 `PRAGMA integrity_check`, `.schema` 확인 필요.
- 둘 다 git 미포함. `data/local_db/` 아래 함께 위치.

---

## 4. 서빙 환경변수 [미정 — README·compose에 확정 기재해야]

| 변수 | 값 | 주의 |
|---|---|---|
| `RETRIEVER_EMBEDDING` | **`e5`** | `config.py`에서 `e5`만 허용한다. 인덱스와 질의가 다른 공간을 사용하지 않도록 `e5-instruct`는 거부한다. |
| `RETRIEVER_MODE` | **`local`** | active 경로는 local SQLite·Chroma만 지원하며 다른 값은 readiness에서 거부 |
| `CLOVA_API_HOST` | `clovastudio.stream.ntruss.com` | |
| `CLOVA_CHAT_MODEL` | 예 `HCX-DASH-002` | 대회 허용 HyperCLOVA X 모델로 확정 |
| `CLOVA_API_KEY` 등 인증 | (NCP CLOVA Studio 키) | git·이미지에 넣지 않음. NCP secret 또는 `.env` |
| Chroma 경로 | `data/local_db/chunk_index_chroma` | 앱 설정이 이 경로를 가리키는지 확인 |
| e5 모델 캐시 | fastembed `cache_dir` | 5장 참고 — 런타임 다운로드 회피 |

---

## 5. 배포 타깃: NCP

### 5.1 컨테이너 전략 [잠정]

- **SQLite(`chunk_index.db`)는 컨테이너 아님.** 앱 컨테이너에 볼륨 마운트(쓰기 안 하면 `:ro`).
- **Chroma는 임베디드 유지**(앱 컨테이너 안 local read-only adapter). 95 GB는 이미지에 굽지 않고 마운트 볼륨.
- **Dockerfile 없음 → 새로 작성 필요** (제출 필수 항목). `docker-compose.yml`도.

### 5.2 컴퓨트 [미정 — 스펙 확정]

- 5.5M 벡터 HNSW. `data_level0.bin` 22 GB를 mmap → **RAM이 쿼리 성능 좌우.** 목표 **RAM 32 GB+**, vCPU 8+.
- fastembed e5-large ONNX 추론은 CPU. 동시 요청 대비 코어 여유.
- NCP Server(VM) 1대 + docker-compose 로 시작. NKS는 과함.

### 5.3 스토리지 [결정]

- **NCP Block Storage** 별도 볼륨(150 GB+) → `/data` 마운트. Chroma 디렉토리 + `chunk_index.db` 배치.
- Object Storage는 POSIX FS 아님 → Chroma 직접 사용 불가. 전송 중간 저장소로만.
- 이미지엔 코드+의존성만.

### 5.4 e5 모델 배포 [잠정]

- fastembed 는 최초 사용 시 HF Hub에서 `intfloat/multilingual-e5-large` ONNX를 받는다.
- 평가 환경 egress 차단 가능성 → **이미지 빌드 시 모델 미리 캐시**하거나 볼륨에 넣고 `cache_dir` 지정. 오프라인 기동 검증.

### 5.5 권장 형태 (콘테스트)

```
NCP Server (RAM 32GB+) + Block Storage 150GB (/data)
└─ docker-compose
   ├─ app  (FastAPI + LangGraph, 임베디드 Chroma)
   │   ├─ volume: /data/chunk_index_chroma        # ~95GB
   │   ├─ volume: /data/chunk_index.db:ro         # SQLite
   │   ├─ volume: /data/models (fastembed cache)  # e5 오프라인
   │   ├─ env: RETRIEVER_EMBEDDING=e5, RETRIEVER_MODE=local, CLOVA_*
   │   └─ requirements.txt: chromadb==1.5.9 핀
```

---

## 6. 데이터 이관 (로컬/Colab → NCP)

1. **Drive 경유 + NCP에서 rclone (권장)** — `rclone copy "gdrive:transfer/..." /data/... -P`, 중단돼도 완전 이어받기.
2. NCP Object Storage(S3 호환) 경유.
3. croc 직접 — 95 GB엔 불안정(런북 함정표). 델타만 보낼 때.

이관 후 검증: `du -sh`(sparse 여부), `PersistentClient` 열어 `count()` == 5,548,951 & `peek()` 정상, `chunk_index.db` `PRAGMA integrity_check`.

---

## 7. 제출물 체크리스트 (09.06)

- [ ] 구현체 소스코드 (제출 repo — **team-feature2 맞는지 확인**)
- [ ] `Dockerfile` (**없음, 작성 필요**)
- [ ] `requirements.txt` (갱신됨 / `chromadb==1.5.9` 핀 완료) + `requirements-langgraph.txt` + `requirements-dev.txt`
- [ ] `README.md` — 환경 구성 + 실행 명령어 + 필수 env (4장) 명시
- [ ] 기술 제안서 (제안 요약/문제 정의/시스템 구성도/기능 흐름도/시나리오/기대효과)
- [ ] 평가용 API: **Endpoint URL** + 요청/응답 JSON 스키마 명세서
- [ ] 대용량(95 GB Chroma + ~20 GB sqlite) 클라우드 스토리지 링크

---

## 8. 리스크 / 게이차

- **임베딩 provider 불일치:** `RETRIEVER_EMBEDDING`을 `e5` 외 값으로 설정하면 readiness에서 거부한다. 인덱스와 질의 모두 `intfloat/multilingual-e5-large`를 사용한다.
- **`chunk_index.db` 미완성:** 다운로드 중. 완료 후 무결성/스키마 확인 전엔 서빙 불가.
- **fastembed 런타임 모델 다운로드:** 오프라인 평가 환경이면 기동 실패. 5.4 대비.
- **langchain-chroma 1.0 ↔ chromadb 1.5.9 호환:** 실제로 `list_collections`/쿼리 되는지 배포 전 확인.
- **HNSW 첫 로드 수십 초~분:** 헬스체크 타임아웃 여유. 예열 후 트래픽.
- **NCP 크레딧:** 09.07~30 상시 가동. 인스턴스 크기·로그 볼륨 관리.
- **RAM 부족 → 스왑 → 지연 폭증.** 스펙 보수적으로.
- **Colab 재빌드 시 e5 차원/모델 변경 → 인덱스 전체 무효.**

---

## 9. 남은 작업 순서 (D-3 기준)

1. `chunk_index.db` 다운로드 완료 → `PRAGMA integrity_check`, `.schema`, 앱에서의 용도 확인
2. `Dockerfile` + `docker-compose.yml` 작성 (env 4장 반영, e5 캐시 포함)
3. 로컬에서 `GET /answer` 스모크 (실 Chroma + sqlite, `RETRIEVER_EMBEDDING=e5`)
4. NCP Server + Block Storage 프로비저닝 → 데이터 이관(6장) → 검증
5. NCP에서 스모크 → **endpoint URL 확정**
6. README + API 명세서 + 기술 제안서 마무리 → 제출 repo push (09.06 전)
7. 09.07 이후 무중단 유지 모니터링 (헬스체크, 크레딧)

---

## 10. 미정 사항

- [ ] 제출 repo = team-feature2 여부 (team-feature / miraeasset-firstpenguin 와의 관계)
- [ ] `CLOVA_CHAT_MODEL` 확정값, CLOVA 인증 방식/키
- [ ] `chunk_index.db` 스키마·용도, 앱 필수 여부
- [ ] NCP 서버 스펙 확정
- [ ] 전송 경로 최종 (Drive+rclone vs Object Storage)
- [ ] 도메인 / HTTPS (평가자 GET 요청 대상 URL — http 허용 여부 확인)
- [ ] 시크릿 관리 방식
- [ ] 콘테스트 종료 후 리소스 정리

---

## 11. 참조

- [`docs/sync-chunk-index.md`](./sync-chunk-index.md) — Colab→로컬 인덱스 동기화 런북
- 대회 자료: `dart_agent_info.pdf`
- 메모리: `chunk-index-sync`
## Retriever·Reasoner process-local cache [결정]

현재 canonical `integration.composition.build_pipeline()`은 pipeline마다 독립적인
`CacheRegistry`를 생성한다. 이 cache는 인덱스나 정답의 source of truth가 아니라,
one-worker 프로세스 안에서만 유효한 bounded TTL/LRU 최적화 계층이다.

```env
DIS164_CACHE_ENABLED=true
DIS164_CACHE_TTL_SECONDS=600
DIS164_CACHE_INDEX_VERSION=1
DIS164_CACHE_QUERY_EMBEDDING_MAX=256
DIS164_CACHE_STRUCTURED_DOC_MAX=512
DIS164_CACHE_FACT_MAX=1024
DIS164_CACHE_CANDIDATE_MAX=16
```

최종 search query의 E5 embedding, 동일 manifest filter의 SQL 후보 문서, 문서별
structured parsing, intent별 Fact extraction만 재사용한다. 최종 ranking·CLOVA Chat·CLOVA
Reranker 응답은 cache하지 않는다. index path metadata와 명시적 version·TTL로 무효화하고,
cache 오류는 기존 계산으로 우회한다. SQLite·Chroma는 read-only로 유지하며 worker 간
Redis 공유 cache는 도입하지 않는다.
