# Stage2

Stage2는 Stage1의 `manifest_filter`를 기준으로 검색 Tool을 실행하고, JSON 또는 SQLite/Chroma
repository에서 구조화된 evidence bundle을 반환하는 공식 검색 단계다.

## 공식 위치

- `stage2/stage2_agent.py`: ChatClovaX 초기화, ToolNode 실행, Stage1 필터 경계 적용
- `stage2/llm.py`: CLOVA provider 설정과 모델 client 생성
- `stage2/embedding.py`: JSON semantic search용 query embedding
- `stage2/json_repository.py`: local JSON fixture adapter
- `stage2/production_repository.py`: SQLite/Chroma production adapter
- `stage2/stage2_repository.py`: repository 계약과 검색 오류 타입

Stage2의 공식 호출은 `integration/composition.py`가 담당한다. 사용자는 workspace 루트에서
`scripts/run_e2e.py`를 실행하며, 이 디렉터리의 모듈을 직접 실행하지 않는다.

`app/tools/`와 `app/schemas/`는 Stage2가 사용하는 검색 Tool과 입력 스키마다. 기존
`app/agent/nodes/stage2.py`는 legacy 단일 그래프 구현이며 공식 Stage2가 아니다.
