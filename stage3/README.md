# Stage3

Stage3는 Stage1의 Intent와 Stage2의 검색·rerank 결과를 입력으로 받아 공시 근거를 구조화하고, 결정론적 계산·비교와 HyperCLOVA X 답변 작성을 수행하는 모듈이다.

현재 구현 단계에서는 Stage1 Intent 입력 계약부터 구현한다. Stage2의 최종 문서 형식은 확정 전까지 별도 어댑터 뒤에 둔다.

## Stage1 입력

```python
from stage3.adapters.stage1 import adapt_stage1_intent

intent = adapt_stage1_intent(stage1_intent_dict, question=question)
```

`route`가 `ok`가 아닌 경우 Stage3는 검색·계산·답변 생성을 진행하지 않고 한계 또는 차단 결과를 반환해야 한다.

## Stage2 입력

```python
from stage3.adapters.stage2 import adapt_stage2_bundle

bundle = adapt_stage2_bundle(stage2_result)
documents = bundle.effective_documents()
```

Stage2의 최종 문서 계약이 확정되기 전까지 `adapt_stage2_bundle()`이 필드 차이를 흡수한다. Stage3는 검색·rerank를 수행하지 않고 전달받은 문서와 근거 구간만 사용한다.

## Fact 추출과 정규화

```python
from stage3.agents.fact_extraction import extract_facts
from stage3.deterministic.normalization import normalize_facts

facts = extract_facts(bundle.effective_documents(), intent)
facts, warnings = normalize_facts(facts, intent)
```

추출 결과는 지표·원값·단위·정규화값·기간·연결/별도 기준·기업·문서 ID·근거 구간을 보존한다. 계산에 사용할 값은 `normalized_value`를 사용하며, 기간·기준이 불명확하면 경고를 남긴다.

## 계산·비교·사건 연결

```python
from stage3.agents.calculation import calculate_facts
from stage3.agents.comparison import compare_facts
from stage3.agents.event_linker import link_events

calculation = calculate_facts(facts, intent)
comparison = compare_facts(facts, intent)
events = link_events(bundle.effective_documents(), intent)
```

계산과 비교는 검색 score가 아니라 정규화된 Fact 값으로 수행한다. 계산 Registry는 whitelist 연산만 실행하며, 기간·단위·연결/별도 기준이 맞지 않으면 결과 대신 오류 상태와 근거를 반환한다.

## Supervisor와 제출 응답

```python
from stage3.service import Stage3Service

service = Stage3Service()
response = service.answer(
    question_id="Q-001",
    question=question,
    stage1_intent=stage1_intent,
    stage2_result=stage2_result,
)
```

`Stage3Service`는 Stage1 route를 먼저 확인한 뒤 Fact·계산·비교·사건 Agent 결과를 통합하고 답변을 작성한다. 최종 `response`는 대회 제출 형식의 5개 문자열 필드로 변환된다. HyperCLOVA X client를 주입하면 답변 생성에 사용하고, client가 없으면 근거 기반 결정론적 템플릿으로 fallback한다.

실제 HTTP 경계는 외부 웹 프레임워크 없이 Python 표준 라이브러리로 제공하며, Stage2 최종 계약을 고정하지 않도록 provider 주입 방식으로 제공한다.

```python
from stage3.api import create_app

app = create_app(stage1_provider=parse_stage1, stage2_provider=retrieve_stage2)
app.serve(host="0.0.0.0", port=8080)
```

`GET /answer`는 인증 헤더 없이 호출되며, provider 오류는 최초 호출 후 최대 2회 재시도한다. 세 번 모두 실패하면 503을 반환한다.

Stage3 패키지는 `agentic_rag`, FastAPI, Pydantic 등 외부 프로젝트·웹 프레임워크에 의존하지 않는다. `requirements.txt`에는 설치 패키지가 없으며, Stage1·Stage2 provider와 HyperCLOVA X client는 실행 환경에서 주입한다.

## 테스트

```powershell
python -m unittest stage3.tests.test_stage1_adapter -v
python -m unittest stage3.tests.test_stage2_adapter -v
python -m unittest stage3.tests.test_fact_extraction -v
python -m unittest stage3.tests.test_calculation_comparison -v
python -m unittest stage3.tests.test_event_linker -v
python -m unittest stage3.tests.test_service_and_api -v
python -m unittest stage3.tests.test_api -v
```
