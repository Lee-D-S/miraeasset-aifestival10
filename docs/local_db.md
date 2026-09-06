# Retriever 로컬 DB — 받아서 테스트하기

Retriever 검색 백엔드는 제공된 인덱스를 사용하는 `local` 모드만 지원한다. 경로와
접속 설정은 모두 루트의 `config.py`가 관리하고, 상대경로는 실행 위치가 아니라
저장소 루트를 기준으로 풀린다.

| 모드 | 저장소 | 필요한 것 | 쓰는 곳 |
|---|---|---|---|
| `local` | SQLite `chunk_index` + 로컬 Chroma 디렉터리 | 빌드된 인덱스 + 질의 임베더 | 실데이터를 단일 노드에서 돌려볼 때 |

아래 1~2절은 팀에서 공유한 `local` 인덱스를 받아서 전체 파이프라인(Interpreter~4)에
물려 돌리는 방법이다.

---

## 1. 빠른 시작 — 공유받은 파일로

### 1-1. 받을 파일

Google Drive 링크에 두 개가 있다.

| 파일 | 크기 | 용도 |
|---|---|---|
| `team-feature2-local-db.tar.gz` | 약 6.5 GB | 인덱스 본체 (압축) |
| `team-feature2-local-db.tar.gz.sha256` | 96 B | 무결성 확인용 체크섬 |

### 1-2. 내려받기

브라우저에서 그냥 받아도 되고, 파일이 커서 중간에 끊기면 `gdown`이 편하다.

```bash
pip install gdown
gdown 'https://drive.google.com/uc?id=<파일ID>' -O team-feature2-local-db.tar.gz
gdown 'https://drive.google.com/uc?id=<체크섬파일ID>' -O team-feature2-local-db.tar.gz.sha256
```

`<파일ID>`는 공유 링크에서 `.../file/d/` 와 `/view` 사이의 문자열이다.

### 1-3. 무결성 확인

두 파일을 같은 폴더에 두고:

```bash
sha256sum -c team-feature2-local-db.tar.gz.sha256
# team-feature2-local-db.tar.gz: OK  <- 이 줄이 나와야 정상
```

`FAILED`가 나오면 받다가 깨진 것이니 다시 받는다.

### 1-4. 압축 풀 위치

**저장소 루트의 `data/` 아래**에 풀어야 한다. `data/`는 `.gitignore`에 있어서
커밋되지 않으니 안심하고 넣으면 된다.

```bash
cd ~/contest/team-feature2          # 저장소 루트
tar -xzf ~/받은경로/team-feature2-local-db.tar.gz -C data/
```

풀고 나면 이렇게 된다. 압축을 풀면 약 18 GB를 차지하니 디스크 여유(내려받은
6.5 GB까지 합쳐 25 GB 정도)를 먼저 확인한다.

```
data/local_db/
  chunk_index.db          # SQLite. chunk_index 테이블 1,155,170행
  chunk_index_chroma/     # Chroma 디렉터리. 컬렉션 chunk_vectors, 800,460 벡터
```

### 1-5. 의존성

세 requirements 파일을 모두 설치한다. `local` 모드의 기본 질의 임베더(`e5`)는
`fastembed`가 필요한데, 아래 설치에 포함돼 있다.

```bash
python3 -m pip install -r requirements.txt -r requirements-langgraph.txt -r requirements-dev.txt
```

---

## 2. `local` 모드로 돌리기

### 2-1. 환경변수

저장소 루트에 `.env` 파일을 만들어 넣는 것이 가장 간단하다(`app.py`가 자동으로
읽는다). export로 직접 잡아도 된다.

```bash
# .env
STAGE2_MODE=local
STAGE2_INDEX_PATH=data/local_db/chunk_index.db
STAGE2_CHROMA_PATH=data/local_db/chunk_index_chroma
STAGE2_CHROMA_COLLECTION=chunk_vectors
STAGE2_EMBEDDING=e5

# 이 인덱스는 벡터 커버리지 69%짜리 부분 빌드다(2-4 참고). 이 플래그가 없으면
# build_pipeline() 이 readiness 검사에서 "Chroma is missing SQLite chunk IDs" /
# "manifest contains documents absent from Retriever index" 로 기동에 실패한다.
STAGE2_ALLOW_PARTIAL_INDEX=true

# Interpreter 은 universe.csv + manifest.jsonl 이 있는 코퍼스 디렉터리가 반드시
# 필요하다(저장소에 커밋돼 있지 않음). data/local_db 를 만든 소스 코퍼스를 가리킨다.
CORPUS_DIR=/absolute/path/to/miraeasset-firstpenguin/data/3.gongsi/corpus

# 아래 키가 있으면 Reasoner(답변 생성)·Validator(의미 검증)까지 실제로 돈다.
# 없으면 Retriever(검색)까지만 정상 동작한다.
CLOVA_API_KEY=...
CLOVA_API_HOST=clovastudio.stream.ntruss.com
```

- `CORPUS_DIR` 없이는 Interpreter 이 `FileNotFoundError` 로 죽는다. 이 코퍼스의
  `manifest.jsonl` 은 인덱스보다 문서 집합이 넓지만(부분 빌드),
  `STAGE2_ALLOW_PARTIAL_INDEX=true` 가 그 불일치 검사를 경고로 강등한다.
- 인덱스에 없는 문서를 Interpreter 이 필터로 잡아도 Retriever 검색에서 자연히 0건이 된다.

### 2-2. 테스트 방법 A — 전체 파이프라인(권장)

서버를 띄우고 `/answer`를 호출하면 `START → interpreter → supervisor → retriever → … →
validator → END` 그래프가 이 인덱스에 대해 그대로 돈다. 파이프라인은 첫 요청 때
지연 초기화되며, `e5` 모델을 처음 한 번 내려받느라 수십 초 걸린다.

```bash
uvicorn app:app --reload

# 다른 터미널에서
curl 'http://127.0.0.1:8000/ready'
curl 'http://127.0.0.1:8000/answer?question_id=q1&question=삼성전자 2023년 3분기 매출액은?'
```

`/answer`는 항상 `question_id`, `question`, `retrieved_context`, `think_trace`,
`answer` 다섯 문자열 필드를 돌려준다. `think_trace`(JSON)에 스테이지별
`status`·`warnings`·`trace`가 들어 있으니 어디서 걸렸는지는 여기서 본다.

CLOVA 키가 없으면 Reasoner는 결정론적 그라운딩으로 답을 만들고 Validator 의미 검증은
통과하지 못해 `validation_failed`로 끝난다. 검색이 제대로 되는지는 이 상태에서도
`retrieved_context`와 `think_trace`로 확인할 수 있다.

### 2-3. 테스트 방법 B — 검색만 빠르게 확인

CLOVA 키 없이 Retriever(이 인덱스를 쓰는 부분)만 바로 확인하고 싶을 때.

```bash
python3 - <<'PY'
from retriever.local_store import LocalHybridRetriever
from retriever.embedding import E5Embeddings

r = LocalHybridRetriever(
    "data/local_db/chunk_index.db",
    chroma_dir="data/local_db/chunk_index_chroma",
    collection_name="chunk_vectors",
    embedding_function=E5Embeddings(),
)
r.initialize()
print("readiness:", r.readiness_issues())

cands = r.filter_candidates({"corp_names": ["삼성전자"]}, limit=80)
print("후보:", len(cands))
for d in r.vector_search("삼성전자 매출액", cands, limit=3):
    print(f"  {d['id']}  {d['vector_score']:.3f}  {d['text'][:70]!r}")
PY
```

정상이면 `readiness`에 `"Chroma is missing SQLite chunk IDs"` 한 줄만 나오고
(뒤 2-4 참고), 후보가 수십 건, `vector_search`가 매출 관련 청크를 돌려준다.

### 2-4. 이 인덱스의 한계

`miraeasset-firstpenguin`의 중단된 부분 빌드를 team-feature2 스키마로 기계 변환한
것이다. 재임베딩은 하지 않았다.

- **벡터 커버리지 69%** — SQLite 1,155,170행 중 800,460행에만 벡터가 있다. 나머지
  35만 행은 벡터 검색에는 안 걸리고 키워드·메타데이터 필터로만 잡힌다. 그래서
  `readiness_issues()`에 "Chroma is missing SQLite chunk IDs" 경고가 항상 뜬다.
  `build_pipeline()` 은 이 경고를 치명으로 취급하므로 `STAGE2_ALLOW_PARTIAL_INDEX=true`
  로 강등해야 기동된다(2-1 참고). 2-3 처럼 retriever 를 직접 쓰면 해당되지 않는다.
- **임베딩 모델 계약** — 공급 Chroma 벡터와 team-feature2의 운영 경로는
  모두 `intfloat/multilingual-e5-large` 1024차원 공간을 사용한다.
  검색 후보도 동일한 모델과 query adapter를 사용해야 하며, 별도 instruct 모델로
  query를 임베딩하면 벡터 공간 계약이 깨진다. 키워드·메타데이터 필터
  (`corp_name`, `section_name`, `rcept_dt` 등)는 별도 SQLite 경로에서 정확하게 적용된다.

---

## 3. 지원하지 않는 모드

과거 `fixture`와 `container` 경로는 현재 active 실행 경로에서 제거했다. 해당 값을
`STAGE2_MODE`에 지정하면 자동 전환 없이 readiness 오류가 발생한다. 기존 SQLite·Chroma
인덱스는 서버에서 read-only로 사용하며, PostgreSQL이나 원격 Chroma로 이관하지 않는다.

---

## 4. 다시 만들거나 새로 공유할 때

- **인덱스를 직접 빌드**: `scripts/build_chunk_index.py`
  (`--corpus-dir`, `--embedding e5|clova`, `--workers`), 또는 Colab
  `scripts/colab_build_chunk_index.py` (→ [`colab_build.md`](colab_build.md)).
- **압축·업로드**: `zstd`/`pigz`가 있으면 그것으로, 없으면 `tar` + `gzip -3`.
  ```bash
  cd data
  tar --use-compress-program='gzip -3' -cf ../team-feature2-local-db.tar.gz local_db
  cd .. && sha256sum team-feature2-local-db.tar.gz > team-feature2-local-db.tar.gz.sha256
  ```
  이 두 파일을 Google Drive에 올리고 링크를 공유한다. GitHub에는 올리지 않는다
  (대용량 바이너리). 업로드가 자주 끊기면
  `split -b 1900M team-feature2-local-db.tar.gz team-feature2-local-db.tar.gz.part-`
  로 쪼개고, 받는 쪽에서 `cat *.part-* > team-feature2-local-db.tar.gz`로 합친다.
- **원본 `miraeasset-firstpenguin/db_tmp/`**(변환 전, `finance_db` 스키마 + UUID id
  Chroma)는 team-feature2에 바로 붙지 않는다. 반드시 변환된 `data/local_db/`를
  공유한다.
