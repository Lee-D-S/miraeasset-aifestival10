# Stage4 — 최종 검증

Stage4는 Stage3 답변의 수치·계산식·출처·citation·의미를 검증한다. 새로운 문서를 검색하거나 Fact를 다시 생성하지 않는다.

```text
Stage3 result + answer
→ numeric validation
→ citation/provenance validation
→ semantic validation
→ success 또는 validation_failed
```

semantic validator가 없으면 검증 불가로 종료한다. 빈 답변과 출처 표기가 없는 답변도 성공으로 처리하지 않는다.

검증 실패 시 Supervisor가 `regenerate_answer`를 선택할 수 있다. 별도 regeneration node가 최대 한 번 답변을 재작성하고 Stage4가 다시 검증한다. 재생성은 검증 판정을 담당하지 않는다.

```python
from stage4 import build_stage4_node
stage4_node = build_stage4_node(validator_client=semantic_client)
```

Stage4는 `stage4_result`, `answer`, `messages`, `validation_attempts`를 반환한다. 최종 API 변환은 `integration.api.to_submission_response()`가 담당한다.
