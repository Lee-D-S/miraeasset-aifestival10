# Enum 및 HyperCLOVA fallback 수정 결과

2026-09-06, 내려받은 `123221a` 코드 기준 로컬 수정. 기존
`enum-fallback-audit.md`는 수정 전 진단 기록이며 이 문서가 수정 후 결과다.

## 확인된 문제와 수정

| 경계 | 수정 후 동작 |
| --- | --- |
| 의도·계산 | 기본 lookup이어도 규칙이 설명하지 못한 표현이 남으면 LLM 의미 검토. 더하기·평균 등 계산 단서를 확장하고 차액을 증감률과 구분 |
| 슬롯 반환값 | 허용 enum 검증 후 지표·의도·연산·공시 그룹/유형·기준월·시간 기준·재무 기준·집계 범위·정정 모드를 반영. 명시적 조건 보존 |
| 질문 타입·라우팅 | 코드가 파생·결정하며 LLM의 임의 route/type을 직접 실행하지 않음. 잘못된 라우팅 값은 fail_closed |
| LLM 실패·미등록 지표 | 호출 상태와 실제 반영 여부를 분리. 의미 검토 실패나 미해결 조건은 재질문 |
| 기간 | 반기 표현을 6월로 반영. 접수연도와 회계연도를 분리. 기간 생략 시 최신 자료 가정 기록 |
| 정정 | latest_only 검색에서 정정본을 배제하지 않음. 식별 가능한 동일 공시 중 최신 접수본의 모든 청크 유지. original_only는 정정 제외 |
| 분석계획 | Compose/예시 설정에서 fallback 활성화. JSON 예산 2048로 분리하고 계획 검증 유지 |
| 근거 추출 | 추출 실패 시 Reasoner 실행당 원문 선택 LLM 복구 최대 1회. 원문·수치·기간·기준 검증을 통과한 Fact만 채택 |
| Fact 종류 | 날짜는 field/date로 통일하고 레거시 date kind 정규화 |
| 이전 단계 복귀 | 근거 부족과 해석 불확실성이 함께 있는 경우 최대 1회 재해석. 기존 검색·계획 상태를 비우고 원 질문으로 재실행 |

## 검증

- 일반 pytest: **340 passed, 61 deselected**.
- corpus pytest: **60 passed, 341 deselected**.
- 새 오류 재현 테스트: **31 passed** (일반 테스트에 포함).
- 규칙 기반 Interpreter checks: **gold 40/40 및 모든 불변식 통과**.
- 실제 HyperCLOVA Interpreter 호출을 포함한 gold: **40/40 기대값 일치**.
  결과는 `enum-fallback-after-live.json`에 보관했다. G01의 정정 필터 기대값은
  최신 정정본 포함이라는 의도적 변경에 맞춰 false에서 null로 변경했다.
- 재해석 그래프와 Fact 복구는 모의 provider로 오류·원문 검증·호출 한도를 검증했다.
  이 결과는 실제 전체 검색/생성 파이프라인 품질 평가를 대신하지 않는다.

재현 명령(저장소 루트, `.env`에 CORPUS_DIR 설정):

```powershell
rtk python -m pytest -q
rtk python -m pytest -q -m needs_corpus
rtk python -c "from dotenv import load_dotenv; load_dotenv(); from interpreter.tests.run_checks import main; raise SystemExit(main())"
rtk python -m scripts.audit_enum_fallback --live --gold --output docs/enum-fallback-after-live.json
```

마지막 명령은 실제 API 비용이 발생하며 모델 응답은 실행마다 달라질 수 있다.
이번 실제 호출에서 '외형 규모'는 자동 매출 매핑 대신 재질문으로 처리됐다.
등록되지 않은 지표를 임의로 새 지표로 실행하지 않는다.
서로 연결하는 메타데이터가 없는 이벤트 정정 공시는 제목만으로 합치지 않는다.
실제 인덱스 전용 테스트와 서버 배포는 이번 완료 범위에 포함되지 않았다.
