# Integration

`integration/`은 Stage1·Stage2·Stage3를 조립하는 orchestration 경계다. 각 Stage의 실제 구현을
보관하지 않으며, 공식 실행 경로는 다음과 같다.

```text
scripts/run_e2e.py
  -> integration/e2e.py
  -> integration/composition.py
  -> stage1/ -> stage2/ -> stage3/
```

- `composition.py`: provider 주입, route gate, 오류 분류, Stage3 handoff
- `e2e.py`: CLI 실행과 내부 결과 출력
- `local_index.py`: JSON fixture에 맞춘 Stage1 local index 구성

Stage2 agent·repository·provider는 `stage2/`에, Stage3 구현은 `stage3/`에 둔다.
