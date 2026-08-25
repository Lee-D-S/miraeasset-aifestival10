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

## 테스트

```powershell
python -m unittest stage3.tests.test_stage1_adapter -v
python -m unittest stage3.tests.test_stage2_adapter -v
python -m unittest stage3.tests.test_fact_extraction -v
```
