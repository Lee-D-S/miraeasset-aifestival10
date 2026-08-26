# Stage 1 + Stage 2 + Stage 3 통합 보고서

상태: Phase 1~5 구현 완료, provider 연결·검색 필터 오류 수정 및 실제 E2E 검증 완료

공식 실행 경로: `scripts/run_e2e.py` → `integration/e2e.py` → `integration/composition.py` →
`app/stage1/` → `integration/stage2_agent.py` → `stage3/`.
`app/agent/`는 legacy 단일 그래프이며 현재 공식 Stage123 E2E 경로가 아니다.

## 구현 위치

이 workspace는 Stage 2 기준본을 복사해 `C:\projects\dis-164\stage123-integrated`에 만들었다. 중첩 Git 저장소는 만들지 않았으며, 변경은 부모 `lds` 브랜치에서 관리한다.

사용자가 제외한 `C:\projects\dis-164\stage123`는 조사·복사하지 않았다.

## 통합 흐름

```text
질문
  -> Stage1 build_intent(LocalJsonCorpusIndex 또는 Production CorpusIndex)
  -> route gate
  -> Stage2 ChatClovaX agent
  -> dart_hybrid_search_tool + LangGraph ToolNode
  -> Stage2Repository(JSON 또는 SQLite+Chroma)
  -> stable id/text/metadata/evidence bundle
  -> Stage3Service
  -> fact extraction / deterministic calculation / comparison / event / answer / validation
  -> 5-string submission response
```

원문 질문은 `original_question`으로 유지하고, retry 검색어는 `search_query`로 분리했다. Stage1 `manifest_filter`는 LLM Tool call에 주입되며 LLM이 기업·기간 필터를 임의로 덮어쓸 수 없다.

## 주요 코드

- `integration/local_index.py`: local JSON의 실제 기업·기간으로 Stage1 `CorpusIndex`를 구성한다.
- `integration/json_repository.py`: `test_data/disclosure_clova_local.json`을 읽고 embedding cosine 검색을 수행한다. query embedding이 없으면 성공으로 위장하지 않고 `embedding_unavailable`로 실패한다.
- `integration/production_repository.py`: 기존 SQLite 후보 조회와 Chroma 검색을 Stage2 구조화 결과로 변환한다.
- `integration/stage2_agent.py`: 실제 ChatClovaX를 lazy 초기화하고 기존 검색 Tool과 LangGraph `ToolNode`를 실행한다.
- `integration/composition.py`: Stage1·Stage2·Stage3 조립, route gate, 오류 분류, 제출 응답을 담당한다.
- `scripts/run_e2e.py`: 단일 자연어 E2E 진입점이다.
- `tests/integration/`: repository·ToolNode·Stage1 route·Stage3 handoff 검증이다.

## 실행

통합 workspace 루트에서 실행한다.

```powershell
rtk python -m unittest discover -s tests/integration -p "test_*.py" -v
rtk python -m unittest discover -s stage3/tests -p "test_*.py" -v
rtk python scripts/run_e2e.py "삼성전자의 2023년 1분기 매출액은 얼마인가?" --include-internal
```

실제 JSON semantic E2E에는 다음이 필요하다.

```text
langchain_naver 설치
CLOVA_API_KEY (CLOVASTUDIO_API_KEY는 레거시 호환용)
LOCAL_JSON_USE_CLOVA_EMBEDDING=1
```

`scripts/run_e2e.py`는 실행 시 workspace의 `.env`를 자동으로 로드한다. `ChatClovaX`에는
`CLOVA_API_KEY`를 명시적으로 전달한다. API Gateway 전용 키는 현재 JSON embedding 경로에 필요하지 않다.

현재 키·호출 사용 현황:

| 환경변수 | 사용 현황 | 호출/역할 |
|---|---|---|
| `CLOVA_API_KEY` | 사용 | Stage2 `ChatClovaX(model="HCX-DASH-002")`, JSON query embedding, 선택적 Stage1 slot filler |
| `CLOVASTUDIO_API_KEY` | 레거시 fallback | `CLOVA_API_KEY`가 없을 때만 통합 caller가 fallback으로 사용 |
| `CLOVASTUDIO_APIGW_API_KEY` | 미사용 | 현재 통합 코드에서 참조하지 않음 |
| `OPENAI_API_KEY` | 미사용 | 현재 통합 코드에서 참조하지 않음; `langchain_naver`의 내부 OpenAI 호환 SDK와 무관 |

현재 실행 설정(`STAGE1_USE_LLM=0`, `STAGE3_EXECUTION_MODE=stdlib`)에서는 Stage1과 Stage3가
별도 LLM을 호출하지 않는다. Stage2만 `langchain_naver.ChatClovaX`를 통해 CLOVA Studio
OpenAI-compatible Chat Completions endpoint의 경량 `HCX-DASH-002` 모델을 호출하고, 검색 질의 embedding은
`/v1/api-tools/embedding/v2`를 직접 호출한다.

키가 없거나 provider package가 없으면 결과의 `think_trace`에 `api_configuration` 또는 `dependency_issue`가 남는다. 해당 결과를 성공으로 집계하지 않는다.

## 문제 원인 및 수정 기록 (2026-08-26)

이번 E2E 점검에서 확인한 문제와 수정은 다음과 같다.

| 증상 | 직접 원인 | 수정 내용 | 검증 |
|---|---|---|---|
| `OpenAIError`/provider 초기화 실패 | `ChatClovaX`가 기본적으로 읽는 키 이름과 프로젝트의 통합 키 이름이 달랐음 | `CLOVA_API_KEY`를 `ChatClovaX(..., api_key=...)`에 명시적으로 전달하고 `CLOVASTUDIO_API_KEY`는 fallback으로만 유지 | 통합 LLM 테스트 통과, `ChatClovaX` 초기화 통과 |
| `APIConnectionError` | 실제 하위 원인은 `[WinError 10013]` 소켓 접근 거부였고, 기존 composition이 예외 원인을 버리고 타입만 기록했음 | `provider_connection` 분류를 추가하고 원인·cause를 마스킹 후 trace에 보존 | 네트워크 허용 실행에서 인증 `/v1/openai/models` HTTP 200, Stage2 ChatClovaX 호출 성공 |
| 검색 후보 0건 | LLM Tool call이 Stage1에 없는 `start_date=20230101~20230131`, `section_name`, `exclude_corp_name` 등을 임의로 추가했고 JSON repository가 이를 그대로 적용했음 | Stage2 Tool call을 `query/top_k`만 자유 입력으로 제한하고 Stage1 manifest 필터를 authoritative하게 재주입 | `candidate_count=9`, `vector_ranked=3` 확인 |
| Windows 한글 JSON 출력 깨짐 | runner가 stdout/stderr 인코딩을 명시하지 않았음 | `scripts/run_e2e.py`에서 출력 스트림을 UTF-8로 재설정 | UTF-8 실행 시 원문 질문·trace 보존 확인 |

### 현재 E2E 결과의 의미

수정 후 실제 전체 흐름은 `Stage1 → ChatClovaX → ToolNode → CLOVA embedding → local JSON → Stage3`까지
실행되며, Stage2 결과는 `ok`이고 문서 3건을 반환한다. 샘플 질문의 최종 상태가
`insufficient_evidence`인 것은 API 실패가 아니다. 현재 21개 chunk fixture에는 사업부문 매출 근거가 중심으로 들어 있고,
총 매출을 안정적으로 식별할 수 있는 구조화된 재무표가 충분하지 않아 Stage3 validator가 숫자를 임의로 확정하지 않고
보수적으로 답변을 보류한다. Production corpus 또는 총계가 포함된 구조화 chunk를 연결한 뒤 같은 질문을 재검증해야 한다.

### 관련·무관 질문 실시간 비교 검증

`test_data/disclosure_clova_local.json`을 backend로 사용하고 실제 CLOVA API를 켠 상태에서 다음 두 질문을 연속 실행했다.

| 케이스 | Stage1 | Stage2/API | 검색 결과 | Stage3 최종 상태 |
|---|---|---|---:|---|
| 삼성전자 2023년 1분기 주요 제품 매출 구성 | `ok` | 실제 ChatClovaX·embedding 호출 성공 | 문서 5건, Fact 10개, citation 4개 | `insufficient_evidence` |
| 현대자동차 2023년 1분기 매출액 | `need_clarify` | route gate로 Stage2 미호출 | 0건 | `need_clarify` |

두 번째 케이스에서 local corpus에 없는 기업을 삼성전자 데이터로 대체하지 않았고, Stage2 검색도 실행하지 않았다.
첫 번째 케이스는 관련 문서를 확보했지만, 현재 fixture의 Stage3 숫자 검증 한계 때문에 답변을 확정하지 않았다.

### 변경 파일별 기록

- `integration/stage2_agent.py`: LLM이 Stage1 기업·기간 경계를 벗어나 추가한 필터를 제거하고 Stage1 필터만 Tool에 전달한다.
- `integration/composition.py`: provider connection/auth/rate-limit 예외를 구분하고, 예외 메시지와 원인을 credential 마스킹 후 보존한다.
- `integration/e2e.py`: Windows stdout/stderr를 UTF-8로 출력한다.
- `tests/integration/test_stage123_flow.py`: 오염된 LLM 필터 회귀 테스트와 `WinError 10013` 원인 보존 테스트를 추가했다.
- `.env`: 실제 키 값은 문서·Git에 기록하지 않는다. 필요한 변수명과 사용처만 이 보고서에 기록한다.

Production DB를 사용할 때는 `E2E_DB_BACKEND=production`, `CORPUS_DIR`, SQLite/Chroma가 모두 준비되어야 한다. 현재 원본 Stage 2 기준본에는 corpus·`db_tmp`가 없으므로 준비 전이다.

## 검증 결과

- 통합 단위·handoff 테스트: 11개 통과
- Stage 3 기존 회귀 테스트: 65개 통과
- Python compileall: 통과
- unsafe route: Stage2 provider 호출 없이 Stage3 blocked response 확인
- 실제 lookup E2E: provider 연결 허용 실행에서 Stage2 `ok`, 후보 9건, vector-ranked 문서 3건 확인
- 제한된 실행 환경에서 동일 호출 시 `[WinError 10013]`이 발생할 수 있으며, 이제 `provider_connection`과 원인 trace로 기록된다.
- 샘플 fixture 최종 결과: Stage3 `insufficient_evidence`; 문서·Fact 일부는 확보했지만 총 매출 확정 근거가 부족해 보류
- Stage 2 Stage1 pytest: 현재 환경에 `pytest`가 없어 별도 실행 대기

실제 provider가 준비되면 lookup·text·period comparison·계산 질의를 다시 실행하고, 문서 ID·Fact·calculation·citation·handoff trace를 케이스별로 기록해야 한다.

## 현재 데이터 한계

기본 local JSON은 삼성전자 중심의 21개 chunk이며 2023-03, 2023-06, 2023-09, 2023-12, 2024-03 기간을 포함한다. 2025년·여러 기업 비교·정정 chain·수시공시가 있다고 가정하지 않는다.

## lds 커밋

- `c08b763 chore: initialize stage123 integration workspace`
- `e6add1f feat: connect stage123 integration workflow`
- `4cd1273 fix: pass unified clova key to ChatClovaX`
- `d70e53a test: use lightweight clova chat model`
- `1ba50c5 fix: diagnose and harden stage123 e2e`

부모 `lds` 브랜치의 기존 미추적 `stage123/` 폴더는 두 커밋에 포함하지 않았다.
