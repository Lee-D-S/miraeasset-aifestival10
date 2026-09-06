# Validator — 최종 검증

Validator는 Reasoner 답변의 수치·계산식·출처·citation·의미를 검증한다. 새로운 문서를 검색하거나 Fact를 다시 생성하지 않는다.

Validator는 `DIS164_STRICT_GROUNDING_V2`가 활성화된 경우 Reasoner Intent의
기업·기간·metric·연결/별도 기준·집계 수준과 일치하는 Fact만 numeric
grounding 대상으로 사용한다. 같은 문서에 있는 다른 부문·다른 기간의
숫자만 답변에 포함되어 있으면 검증을 통과시키지 않는다.

```text
Reasoner result + answer
→ numeric validation
→ citation/provenance validation
→ semantic validation
→ success 또는 validation_failed
```

semantic validator가 없거나 provider 호출이 실패하면 numeric·citation·Fact grounding
local gate로 fallback한다. 이 gate를 통과하지 못하면 fail-closed하며, 빈 답변과 출처
표기가 없는 답변도 성공으로 처리하지 않는다.

semantic prompt는 Reasoner 답변에 실제로 포함된 수치와 일치하는 Fact 및 citation을
우선 전달한다. 한 공시에서 연결·부문·종속기업 Fact가 함께 추출될 수 있으므로 단순히
앞부분만 절단해 대표 근거가 누락되지 않도록 한다.

semantic provider가 판단을 유보하더라도 Validator는 numeric·citation 검증을 모두 통과한
명시적 근거 답변만 제한적으로 보완 통과시킨다. numeric Fact가 있는데 답변에 숫자가
없으면 실패시켜 일반적인 안내문을 성공 답변으로 허용하지 않는다.

다중 `query_plan` 결과는 subquery별로 Fact gate를 적용한다. 일부 subquery가
`insufficient_evidence`여도 다른 subquery의 명시적 Fact·citation이 답변을
지지하면 `partial_success` 답변을 검증할 수 있으며, 누락된 subquery ID는
numeric check 오류에 기록한다. 모든 subquery에 근거가 없으면 fail-closed한다.

Validator는 provider의 generic한 불확실성만 있고 unsupported claim·missing aspect가 없을
때에 한해, 결정론적 numeric·citation 검증 결과를 최종 grounding 근거로 사용한다.

검증·근거 부족으로 답변을 차단할 때는 `integration.failure_response`가 기존의 안전한
결론 문장을 첫 문장으로 유지하고, 사용자 조치가 가능한 이유와 필요한 경우 재질문
안내를 결정론적으로 덧붙인다. 공개 답변은 약 300자 이내로 제한하며, 내부
`failure_reason_code`는 `validator_result`와 `think_trace`에만 기록한다. API key, 원시
provider 오류, 검색 점수와 같은 내부 정보는 답변에 포함하지 않는다.

검증 실패 시 Supervisor가 `regenerate_answer`를 선택할 수 있다. 별도 regeneration node가 최대 한 번 답변을 재작성하고 Validator가 다시 검증한다. 재생성은 검증 판정을 담당하지 않는다.

```python
from validator import build_validator_node
validator_node = build_validator_node(validator_client=semantic_client)
```

Validator는 `validator_result`, `answer`, `messages`, `validation_attempts`를 반환한다. 최종 API 변환은 `integration.api.to_submission_response()`가 담당하며, 상위 응답 필드는 기존 다섯 문자열을 유지한다.
