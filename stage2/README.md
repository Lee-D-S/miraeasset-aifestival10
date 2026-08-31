# Stage2

Stage2는 Stage1 Intent를 받아 공시 근거 문서를 검색하고 Stage3에 넘기는 검색 전용 모듈이다.
`manifest_filter.exclude_corp_names`가 있으면 기업 목록·섹터 조건보다 우선해 해당 기업의
문서를 제외한다.

현재 canonical fixture 경로는 `legacy/test_data/disclosure_clova_local.json`을
`JsonFixtureRetriever` adapter로 읽는다. query embedding은 `ClovaQueryEmbedding`을
주입하며 provider가 없으면 `embedding_unavailable`로 종료한다. 테스트와 계약 검증에는
`InMemoryRetriever`와 deterministic reranker를 명시적으로 주입한다.

실행 factory는 `STAGE2_MODE`로 세 모드를 연다(경로·연결 문자열은 모두 루트의 `config.py`가
관리한다).

```text
STAGE2_MODE=fixture     # CLOVA 사전계산 임베딩 JSON. DB 서비스 불필요
STAGE2_MODE=local       # 로컬 SQLite(metadata) + 로컬 Chroma persist 디렉터리(vector)
STAGE2_MODE=container   # Dockerized Postgres + Chroma 서버. RDB_URL·CHROMA_HOST 필수
```

구 `STAGE2_BACKEND=fixture|sqlite`도 계속 인식된다(`sqlite`는 `STAGE2_RDB_URL`/
`STAGE2_CHROMA_HOST` 설정 여부에 따라 `local` 또는 `container`로 매핑). `local`·`container`는
`STAGE2_CHROMA_COLLECTION`(기본 `stage2_chunks`)으로 같은 벡터 컬렉션을 가리킨다.

`STAGE2_FIXTURE_PATH`/`STAGE2_INDEX_PATH`/`STAGE2_CHROMA_PATH`가 빈 문자열이면 `config.py`의
기본 경로를 쓰고, 상대경로는 실행 CWD가 아니라 프로젝트 루트를 기준으로 해석된다.
`local`·`container` 모드에서도 사용자 질의는 저장된 문서와 동일한 CLOVA Embedding v2
차원으로 임베딩되어 hybrid 검색에 사용된다. provider가 없으면 fake embedding으로
대체하지 않고 `embedding_unavailable`로 종료한다. canonical runtime은 Stage3가
종속기업·사업부 행보다 연결 총계 행을 회복할 수 있도록 최종 cited 문서를 최대 20개
전달하며, deterministic 테스트 factory는 기존 8개 제한을 유지한다.

```python
from stage2 import InMemoryRetriever, build_stage2_node

node = build_stage2_node(retriever=InMemoryRetriever([]))
```

## Local SQLite smoke index

The selected real-document smoke corpus can be stored in SQLite after document
chunking and embedding. The generated database is local and is not committed
to Git.

```bash
python scripts/build_local_smoke_index.py --source-root "<competition-root>"
python scripts/build_local_sqlite.py
```

The embedding builder sends requests sequentially. `--request-delay` controls
the delay after successful calls, and HTTP 429 responses use the provider's
`Retry-After` or `x-ratelimit-reset-requests` header when available; otherwise
exponential backoff is used. The builder also supports `--doc-id` and `--term`
to create a small targeted index without embedding the entire corpus.

CLOVA's published test/web limit for Embedding v2 is 60 QPM and 40,000 TPM,
but the limit is not a processing guarantee. `42901` means the usage limit was
exceeded and `42902` indicates service overload, so callers must still retry
with a bounded delay. See the [CLOVA usage control policy](https://guide.ncloud-docs.com/docs/clovastudio-ratelimiting).

`build_local_sqlite.py` reads `data/local_smoke/embedded_chunks.json` and writes
`data/local_smoke/smoke.db`. `LocalHybridRetriever` stores chunk metadata in SQL
and embeddings in Chroma, and exposes the same metadata, keyword, and vector
search methods as the Stage2 retriever contract. The SQL and Chroma stores are
written together; the existing fixture JSON remains the offline default.
The query embedder must be injected for vector search; the repository never
silently substitutes a fake embedding provider.

## Troubleshooting real-document smoke tests

If Stage2 succeeds but Stage3 reports insufficient evidence, first check the
indexed chunk range. The first chunks of a disclosure often contain cover and
metadata sections rather than financial tables. Use `--doc-id` and `--term`
to filter chunks *before* embedding:

```bash
python scripts/build_local_smoke_index.py --source-root "<competition-root>" \
  --doc-id periodic_20260310002820 --term 매출액 \
  --max-chunks-per-document 0 --request-delay 2
```

Do not interpret mojibake in a terminal as proof that the XML is corrupted.
Check the decoded Python text and code points first. Also normalize disclosure
folder and manifest paths to NFC because the supplied raw folder names may use
NFD. If Stage4 fails after Stage3 creates facts and citations, inspect numeric,
citation, and semantic checks independently; a provider HTTP error in semantic
validation must remain a validation failure rather than a successful answer.

실제 smoke corpus 전처리는 `stage2.ingestion.build_chunk_rows()`를 사용한다. 선택 목록은
`data/local_smoke/selected_documents.json`이며, XML 원문·metadata를 읽어 deterministic chunk를
생성한다. 이 함수는 embedder를 명시적으로 주입하지 않으면 임베딩을 생성하지 않는다.

노드는 `stage2_result`, 호환용 `documents`, 검색 시도 횟수와 검색어를 작성한다. 답변 생성,
계산, context 작성, 계약·정정공시 관계 해석은 Stage3 또는 Stage4의 책임이다.

실제 CLOVA query embedding timeout은 `CLOVA_EMBEDDING_TIMEOUT`(기본 30초)으로
설정한다. 답변 생성·semantic validation은 `CLOVA_CHAT_TIMEOUT`(기본 60초), 429
재시도 횟수는 `CLOVA_CHAT_MAX_RETRIES`(기본 1회), 재시도 대기 상한은
`CLOVA_RATE_LIMIT_MAX_WAIT`(기본 15초)로 제한한다.
각 adapter는 마지막 응답의 `x-ratelimit-*` 헤더를 `last_rate_limit`에 보존한다.
호출 전 process-local limiter가 embedding은 60 QPM/40,000 TPM, chat은 90 QPM/80,000
TPM의 기본 예산을 기준으로 부족한 요청을 차단한다. 실제 provider header를 관찰하면
잔여량과 reset 시간을 우선 반영한다.
chat 출력 토큰은 답변 512, semantic validation 256을 기본 상한으로 두며
`CLOVA_ANSWER_MAX_TOKENS`, `CLOVA_SEMANTIC_MAX_TOKENS`로 조정할 수 있다.
