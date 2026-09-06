# 사전 정의 값·LLM fallback 점검

검증일: 2026-09-06. 이전 fallback 수정이 적용된 로컬 작업 트리 기준.

## 결론

일부 누락 슬롯 보완은 작동하지만, 표 전체에 대해 미해석 표현을 감지하고
LLM으로 복구하는 구조는 아니다. 특히 규칙이 의미를 놓친 채 `lookup`, `annual`,
`연결`, `total`로 처리하면 완료된 해석으로 취급되어 LLM을 호출하지 않는다.
이전 gold-query 통과는 해당 질문들의 회귀 확인이며 전체 표현의 의미 정확성을 보장하지 않는다.

## 항목별 판정

| 항목 | 누락·미등록 표현 처리 | LLM/실패 경로 | 판정 |
| --- | --- | --- | --- |
| 질문 의도 | unknown이면 보완; 기업이 잡히면 단서가 없어도 lookup으로 확정 | unknown만 덮어쓸 수 있음. 잘못 확정된 lookup은 재해석하지 않음 | 불완전 |
| 질문 타입 | 의도·비교축·계산 연산에서 결정적으로 파생 | LLM 응답의 question_type은 무시 | 파생 자체는 정상, 의도 오류 상속 |
| 라우팅 상태 | 규칙이 생성. 기업 부족은 need_clarify, 범위 밖은 unanswerable, 금지 질의는 unsafe | LLM이 직접 route를 지정하지 못함 | 정상 경로 확인. 비정상 state 경계는 별도 문제 |
| Metric | 미확정이면 LLM 호출. 20개 등록 값 모두 병합 가능 | 미등록 metric은 폐기. 여전히 없으면 경고와 함께 route=ok로 검색 | 보완 동작, 미해결 시 엄격한 차단은 아님 |
| 계산 연산 | calc인데 연산 없음, ratio_percent 분모 없음이면 호출 | 13개 허용 연산 병합 가능. 기존 잘못된 연산은 유지 | 의미 감지 누락·오분류 재현 |
| 분석계획 연산 | 결정적 컴파일 실패 시에만 별도 LLM 제안 | QUERY_PLANNER_LLM_ENABLED 필요. 로컬 미설정으로 비활성. 불량 계획은 unavailable, 재시도 한도 후 fail_closed | 경로 존재, 로컬에서는 꺼짐 |
| 공시 그룹 | 지표 정의로 결정. LLM 호출 중 비어 있으면 4개 그룹 보완 가능 | 그룹 누락 자체는 호출 조건 아님. 최종 enum 검증 있음 | 조건부 지원 |
| 정기공시 유형 | 연도만 있으면 annual, 해당 연도에 없으면 가능한 보고서로 기본값 조정 | 유형 미인식만으로 호출하지 않음. LLM이 subtype만 채우면 월 정보 누락 가능 | 결함 재현 |
| 시간 기준 | periodic은 fiscal, major/exchange/holding은 disclosure 우선 | 직접 LLM 보완 필드 없음. 정기공시 제출연도 표현도 fiscal로 분류 | 문맥 미지원 재현 |
| 정정 처리 | 기본 latest_only, 정정·원공시 등 단서가 있으면 include_chain | original_only로 분류하는 추출 경로 없음. LLM 필드도 없음 | 결함 재현 |
| 집계 범위 | 문맥 규칙으로 product/region/segment, 요청은 그 외 total | 직접 LLM 보완 없음. Fact의 unknown은 total 요청에 허용 | 바꿔 말한 범위 누락 재현 |
| Fact 종류 | 문서에서 정규식·구조로 추출. 숫자는 numeric, 필드는 field, 서술은 text | 추출 실패 시 LLM Fact 추출 없음. 미등록 kind는 grounding에서 제외 | 결정적 처리. 실제 내부에는 date도 존재 |
| 재무 기준 | 연결·별도·개별기준 단서; 금융 지표는 누락 시 연결 기본값 | basis는 슬롯 LLM 스키마에 없어 반환해도 무시 | 바꿔 말한 별도 기준 누락 재현 |

연도 자체 누락은 표의 시간 기준(fiscal/disclosure)과 다르다. 연도 누락은
현재 fallback 조건에 포함되어 실제 LLM 보완을 확인했다.

## 실제 질문에서 재현한 문제

아래는 최종 답변이 아니라 Interpreter 출력과 다음 Supervisor action 기준의 판정이다.

| 질문 | 실제 결과 | 문제 |
| --- | --- | --- |
| 삼성전자 2024년과 2025년 매출액을 더한 값은? | lookup, calculation={}, LLM 0회, run_retriever | add를 감지하지 못함 |
| 삼성전자 2024년과 2025년 매출액의 평균은? | lookup, calculation={}, LLM 0회 | average를 감지하지 못함 |
| 삼성전자 2024년 대비 2025년 매출액의 차액은? | percentage_change, LLM 0회 | 금액 차이를 증가율로 처리 |
| 삼성전자 2025년 첫 여섯 달 매출액은? | annual, base_months=[12], LLM 0회 | 상반기 표현을 연간으로 처리 |
| 삼성전자가 2025년에 제출한 사업보고서 매출액은? | fiscal, base_years=[2025], LLM 0회 | 제출연도와 회계연도 구분 누락 |
| 삼성전자 2025년 공급계약 정정본을 제외하고 원공시만 알려줘 | include_chain, LLM 0회 | original_only가 되지 않음 |
| 삼성전자 2025년 공급계약 정정 후 최종 내용만 알려줘 | include_chain, LLM 0회 | latest_only가 되지 않음 |
| 삼성전자 2025년 국내와 해외로 나눈 매출액은? | scope=total, LLM 0회 | 지역 범위를 놓침 |
| 삼성전자 2025년 자회사를 빼고 본사만의 매출액은? | basis=연결, LLM 0회 | 별도 취지 표현을 놓침 |

추가 주입 재현: LLM이 `2026년 + half`를 보완하면 `base_months=[]`,
`period_explicit=False`라서 코퍼스에 없는 2026년 반기를 `route=ok`로 통과시킨다.
정기공시 유형을 보완할 때 월과 명시 기간 여부를 일관되게 갱신해야 한다.

정정 처리 추가 주의: 기본 latest_only인데 manifest_filter.is_correction=False가 적용된다.
Retriever는 이 값을 그대로 필터하므로 정정 문서는 검색에서 제외된다. latest_only의
원본·최신 정정 선택 의미와 원본만 검색하는 필터가 일치하는지 수정 시 함께 다뤄야 한다.

## 실제 HyperCLOVA 호출 확인

22개 질문 중 7개에서 슬롯 호출이 발생했다. 실제 결과가 반영된 것은 5개이며,
이 수는 정답률이 아니다. 기업이 없는 질문에서도 의도만 채워지면 llm_used=True다.

- 외형 규모 → metric=revenue 보완 성공.
- 이천이십오년 → years=[2025] 보완 성공.
- 이천이십오년 상반기 → 연도 보완, 규칙으로 이미 추출된 half/[6] 유지.
- 고객 이탈률 → 미등록 지표를 임의로 등록하지 않음. metric=None, missing_slots=[metric], route=ok.
- 기간 없는 매출 질문 → 연도를 추측하지 않음. missing_slots=[time], route=ok.
- 기업 없는 질문 및 삼성처럼 모호한 기업 → need_clarify 유지.
- 모호한 삼성 질문에서 의도를 calc로 잘못 채우는 응답도 관찰했다.
  이 경우 기업 모호성에 의해 재질문으로 차단되었다. enum 검증은 의미 검증이 아니다.

최초 샌드박스 네트워크 시도는 URLError와 이후 로컬 rate-limit으로 실패했다.
권한을 통한 재실행 결과를 live JSON에 저장했다. 마지막 RuntimeError 로그는
진단 코드가 의도적으로 주입한 provider 실패 테스트이며 실서비스 호출 오류가 아니다.

## 실패 후 돌아가는 경로

- Interpreter LLM 실패/잘못된 enum: 기존 규칙 결과 유지. 기업 부족이면 재질문,
  지표·기간 부족은 현 minimal 정책상 경고만 남기고 검색한다.
- 검색 결과 없음: 검색 쿼리를 바꾸어 1회 재시도, 또 없으면 unanswerable.
- 계산계획 없음: planner 제한 횟수 안에서 생성 시도, 실패하면 fail_closed.
- Reasoner insufficient_evidence: 기본적으로 fail_closed. 계획 누락 예외를 제외하면
  Interpreter로 되돌아가 질문을 재해석하거나 LLM으로 Fact를 재추출하지 않는다.
- 답변 모델 오류: 근거 기반 결정적 템플릿 fallback.
- 답변 검증 실패: 설정상 가능하면 1회 재생성, 이후 fail_closed.

경계 주입에서 route=bogus/빈 문자열은 Supervisor가 run_retriever로 통과시켰다.
Reasoner adapter는 알 수 없는 route를 unanswerable로 정규화하지만 그보다 앞선
Supervisor 검증은 없다. 현재 정상 Interpreter는 허용 route만 만들므로 외부 state 오류에
대한 방어 누락이며 일반 자연어 질문에서 발생했다고 주장하지 않는다.

## 검증 범위와 산출물

- 기본 로컬 테스트 305개 통과, 61개 데이터 의존 테스트는 기본 설정상 제외.
- 등록 Metric 20/20, Interpreter 연산 13/13 병합 확인.
- 의도 6개, 공시 그룹 4개, 정기 유형 3개 병합과 미등록 값 폐기 확인.
- 분석계획 연산 14개는 enum 검증 통과, median은 거부. 이는 계획 스키마 검증이며
  모든 산식의 실수치 실행 정확도를 새로 검증했다는 뜻은 아니다.
- 잘못된 Fact kind는 grounding에서 제외, 실제 내부 date Fact 생성 확인.
- 실패·빈 응답·미등록 값·현재 스키마에 없는 필드 주입 결과 기록.

재현 명령(저장소 루트):

```powershell
rtk python -m scripts.audit_enum_fallback --output docs/enum-fallback-probe.json
rtk python -m scripts.audit_enum_fallback --live --output docs/enum-fallback-live.json
rtk python -m pytest -q
```

[진단 스크립트](../scripts/audit_enum_fallback.py), [주입 결과](enum-fallback-probe.json),
[실제 LLM 결과](enum-fallback-live.json).

이번 점검에서는 진단 코드와 결과 문서만 추가했다. 위에서 새로 발견한 결함의
운영 로직 수정, 서버 배포, 전체 검색·답변 정확도 검증은 수행하지 않았다.

## 수정 우선순위

1. 의미를 놓친 기본값과 실제 확정값을 구분하여 계산·기간·기준·범위의 미해석을 감지한다.
2. 보완 스키마와 병합을 확장하고 period/basis/correction/scope의 하위 검색·계산 조건까지 갱신한다.
3. 실패 사유에 따라 재해석 가능 오류와 근거 자체 부족을 분리한다. 무조건 재질문/재호출로 보내지 않는다.
4. 별도 planner 스위치와 출력 한도를 검증한다. 현재 planner는 여전히 semantic 출력 한도(로컬 256)를 공유한다.
5. 위 오해석 질문들을 기대 의미 기준 회귀 테스트에 추가한다.
