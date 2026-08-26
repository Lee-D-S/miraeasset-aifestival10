# Stage 1 + Stage 2 + Stage 3 통합 보고서

상태: Phase 1~5 구현 완료, 실제 외부 provider E2E는 환경 의존성으로 대기

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
CLOVASTUDIO_API_KEY 또는 CLOVA_API_KEY
LOCAL_JSON_USE_CLOVA_EMBEDDING=1
```

키가 없거나 provider package가 없으면 결과의 `think_trace`에 `api_configuration` 또는 `dependency_issue`가 남는다. 해당 결과를 성공으로 집계하지 않는다.

Production DB를 사용할 때는 `E2E_DB_BACKEND=production`, `CORPUS_DIR`, SQLite/Chroma가 모두 준비되어야 한다. 현재 원본 Stage 2 기준본에는 corpus·`db_tmp`가 없으므로 준비 전이다.

## 검증 결과

- 통합 단위·handoff 테스트: 6개 통과
- Stage 3 기존 회귀 테스트: 65개 통과
- Python compileall: 통과
- unsafe route: Stage2 provider 호출 없이 Stage3 blocked response 확인
- 실제 lookup E2E 시도: `langchain_naver` 미설치로 `dependency_issue` 기록
- Stage 2 Stage1 pytest: 현재 환경에 `pytest`가 없어 별도 실행 대기

실제 provider가 준비되면 lookup·text·period comparison·계산 질의를 다시 실행하고, 문서 ID·Fact·calculation·citation·handoff trace를 케이스별로 기록해야 한다.

## 현재 데이터 한계

기본 local JSON은 삼성전자 중심의 21개 chunk이며 2023-03, 2023-06, 2023-09, 2023-12, 2024-03 기간을 포함한다. 2025년·여러 기업 비교·정정 chain·수시공시가 있다고 가정하지 않는다.
