# Stage123 통합 공시 Agent

미래에셋 AI 페스티벌용 Stage1 → Stage2 → Stage3 통합 workspace다.

## 공식 실행 경로

현재 공식 E2E 진입점은 `scripts/run_e2e.py`다.

```text
질문
  -> scripts/run_e2e.py
  -> integration/e2e.py
  -> integration/composition.py
  -> Stage1: stage1/ + LocalJsonCorpusIndex 또는 Production CorpusIndex
  -> Stage2: stage2/stage2_agent.py + dart_hybrid_search_tool
  -> Stage2Repository: JSON fixture 또는 SQLite/Chroma adapter
  -> Stage3: stage3/ + Stage3Service
  -> 제출 응답 5개 문자열
```

`app/agent/agent.py`는 현재 공식 E2E 진입점이 아니다. 기존 단일 LangGraph 그래프를 보존한
legacy 경로이며, 자세한 내용은 [app/agent/README.md](app/agent/README.md)를 참고한다.

## Stage별 공식 위치

| 단계 | 공식 위치 | 역할 |
|---|---|---|
| Stage1 | `stage1/` | 자연어 질문을 Intent와 route, manifest filter로 변환 |
| Stage2 | `stage2/stage2_agent.py` | ChatClovaX 호출, 검색 Tool 실행, Stage2 evidence bundle 생성 |
| Stage2 검색 | `app/tools/hybrid_db_tools.py`, `stage2/*_repository.py` | JSON 또는 SQLite/Chroma 검색 adapter |
| Stage3 | `stage3/` | Fact, 계산, 비교, 이벤트, 답변, 검증 |
| 통합 조립 | `integration/composition.py` | Stage1·Stage2·Stage3 실행 순서와 오류 경계 |

`app/agent/nodes/stage2.py`는 legacy 그래프의 Stage2 구현이며 현재 공식 통합 Stage2가 아니다.
`stage3/`가 루트에 있는 이유는 Stage3가 독립 계약·stdlib 실행 모드를 가진 standalone package이기
때문이다. 현재 통합 경로에서는 `integration/composition.py`가 이 package를 호출한다.

## 실행 환경

`.env`에 다음 설정을 둔다. 실제 키 값은 Git에 저장하지 않는다.

```text
CLOVA_API_KEY=<실제 키>
E2E_DB_BACKEND=json
LOCAL_JSON_USE_CLOVA_EMBEDDING=1
STAGE1_USE_LLM=0
STAGE3_EXECUTION_MODE=stdlib
```

현재 기본 테스트는 `test_data/disclosure_clova_local.json`을 사용한다.
`E2E_DB_BACKEND=json`이 기본값이며, `LOCAL_JSON_DB_PATH`가 없으면 이 fixture를 자동으로 찾는다.

- Stage1: 기본적으로 규칙 기반이며 LLM을 호출하지 않는다.
- Stage2: `ChatClovaX(model="HCX-DASH-002")`가 실제 CLOVA API를 호출한다.
- JSON 검색: `LOCAL_JSON_USE_CLOVA_EMBEDDING=1`이면 검색어 embedding을 CLOVA API로 생성한다.
- Stage3: 기본 `stdlib` 모드에서 결정론적으로 실행한다.
- RAG Reasoning API: 현재 Stage123 통합 경로에서는 호출하지 않는다.

현재 통합 경로의 키 사용은 다음과 같다.

| 환경변수 | 상태 | 용도 |
|---|---|---|
| `CLOVA_API_KEY` | 사용 | Stage2 ChatClovaX와 JSON query embedding |
| `CLOVASTUDIO_API_KEY` | legacy fallback | `CLOVA_API_KEY`가 없을 때만 fallback |
| `CLOVASTUDIO_APIGW_API_KEY` | 미사용 | 현재 Stage123 통합 코드에서 호출하지 않음 |
| `OPENAI_API_KEY` | 미사용 | 현재 Stage123 통합 코드에서 호출하지 않음 |

따라서 JSON fixture를 사용해도 `LOCAL_JSON_USE_CLOVA_EMBEDDING=1`이면 실제 CLOVA API가
호출된다. 키를 제공하지 않은 fixture-only 테스트는 Stage2 provider를 성공으로 가장하지 않고
`api_configuration` 또는 `dependency_issue`로 종료한다.

## 테스트

workspace 루트(`C:\projects\dis-164\stage123-integrated`)에서 실행한다.

```powershell
rtk python -m unittest discover -s tests/integration -p "test_*.py" -v
rtk python -m unittest discover -s stage3/tests -p "test_*.py" -v
rtk python -m compileall -q app dart_preprocessing integration stage3 scripts tests
rtk python scripts/run_e2e.py "삼성전자의 2023년 1분기 주요 제품 매출 구성은 어떻게 되어 있나요?" --include-internal
rtk python scripts/run_e2e_suite.py
```

`run_e2e.py`는 질문 하나를 실행하는 단건 진입점이고, `run_e2e_suite.py`는 JSON fixture의
관련 질문·무관 기업 질문·unsafe·clarify·기간 밖 질문을 연속 실행해 API 모드, 문서 ID,
인용 수, 실패 분류를 JSON으로 출력하는 회귀용 suite다.

실제 E2E에서 provider 연결과 검색이 성공해도 fixture의 근거가 부족하면 Stage3는
`insufficient_evidence`를 반환한다. 이는 API 성공 여부와 별개의 데이터·검증 결과다.

## 선택적 Production DB 경로

SQLite와 Chroma를 사용하는 경로도 구현되어 있지만 현재 기본 테스트 경로는 아니다.
Production corpus(`universe.csv`, `manifest.jsonl`, raw 문서)와 DB가 준비된 경우에만 다음 설정을 사용한다.

```text
E2E_DB_BACKEND=production
CORPUS_DIR=<universe.csv와 manifest.jsonl이 있는 corpus 경로>
```

`dart_preprocessing/`는 이 선택적 production DB 전처리 경로다. 파일 하나씩 수동 처리하는 방식이
아니라 전체 corpus를 배치 처리해 SQLite/Chroma를 만든다. 상세한 입력·임베딩 경계는
[`dart_preprocessing/README.md`](dart_preprocessing/README.md)를 참고한다.

## 폴더 구조

```text
stage1/                # 공식 Stage1
stage2/                # 공식 Stage2 agent·repository·provider
app/
  schemas/              # Stage2 Tool 입력 스키마
  tools/               # 공식 검색·계산 Tool
  agent/               # legacy 단일 Agent 그래프
integration/            # 공식 Stage123 조립·E2E
stage3/                # 공식 Stage3 standalone package
dart_preprocessing/    # 선택적 Production DB 전처리
scripts/                # 공식 실행 스크립트
  maintenance/         # 선택적 corpus 유지보수 스크립트
docs/                  # 통합 검증 보고서
tests/integration/      # Stage123 통합 테스트
stage3/tests/           # Stage3 회귀 테스트
test_data/              # 부모 workspace의 JSON fixture
```

`stage123/`라는 별도 미추적 폴더는 기존 작업에서 제외한 폴더이며 이 workspace의 공식 경로가 아니다.

## 현재 검증 상태

- Stage123 통합 테스트 11개 통과
- Stage3 회귀 테스트 65개 통과
- Python compileall 통과
- 실제 CLOVA ChatClovaX·embedding API를 포함한 E2E 실행 확인
- 관련 질문은 문서를 검색하고, corpus에 없는 기업은 route gate에서 차단
- fixture에 사업부문 근거는 있어도 총 매출 구조화 근거가 부족한 질문은
  `insufficient_evidence`로 보류될 수 있으며, 이는 API 실패가 아니다.

세부 원인·검증 로그·커밋 기록은 [docs/INTEGRATION_REPORT.md](docs/INTEGRATION_REPORT.md)에 기록한다.
