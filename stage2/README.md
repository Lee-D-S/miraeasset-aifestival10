# Stage2

Stage2는 Stage1 Intent를 받아 공시 근거 문서를 검색하고 Stage3에 넘기는 검색 전용 모듈이다.
`manifest_filter.exclude_corp_names`가 있으면 기업 목록·섹터 조건보다 우선해 해당 기업의
문서를 제외한다.

## 백엔드

실행 factory는 `STAGE2_MODE`로 백엔드를 선택한다.

```text
fixture     CLOVA 사전계산 임베딩 JSON. CI·오프라인 계약 테스트용
local       read-only SQLite chunk_index + local Chroma persist directory
container   Postgres + Chroma 서버
```

기본 모드는 `fixture`다. `local` 모드의 기본 인덱스는 다음과 같다.

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
STAGE2_EMBEDDING=e5-instruct
STAGE2_ALLOW_PARTIAL_INDEX=true
```

`e5-instruct`는 `intfloat/multilingual-e5-large-instruct`를 사용하며 제공 Chroma 벡터와
같은 raw-text·정규화 설정으로 질의를 임베딩한다. `sentence-transformers`가 필요하고,
모델은 서버에 미리 캐시되어 있어야 한다. 서버 시작 시 외부 다운로드는 하지 않는다.

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
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 1
```

외부에서 `/health` → `/ready` → `/answer` 순서로 확인한다. `/answer`는
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer`를 모두 문자열로
반환한다.

fixture는 `STAGE2_MODE=fixture`로 명시하면 사용할 수 있다. fixture와 결정론적 테스트는
실제 DB가 없는 CI·오프라인 환경을 위한 것이며, production fallback으로 사용하지 않는다.

## 안전 경계

- 제공 SQLite·Chroma 인덱스는 read-only로 연다.
- `chunk_id`가 SQLite와 Chroma에서 일치하는지 readiness에서 확인한다.
- 부분 인덱스 경고는 명시적 플래그가 있을 때만 허용한다.
- OpenDART·뉴스·리포트 등 외부 데이터는 Stage2에서 사용하지 않는다.
- API key와 요청 원문은 로그·응답의 trace에 기록하지 않는다.
