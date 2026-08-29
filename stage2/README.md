# Stage2

Stage2는 Stage1 Intent를 받아 공시 근거 문서를 검색하고 Stage3에 넘기는 검색 전용 모듈이다.

현재 구현은 DB나 임베딩 모델을 로드하지 않는다. `Stage2Retriever`를 주입하면 SQLite·Chroma
adapter를 나중에 추가할 수 있으며, 테스트와 계약 검증에는 `InMemoryRetriever`를 사용한다.

```python
from stage2 import InMemoryRetriever, build_stage2_node

node = build_stage2_node(retriever=InMemoryRetriever([]))
```

노드는 `stage2_result`와 호환용 `documents`만 작성한다. 답변 생성, 계산, context 작성,
계약·정정공시 관계 해석은 Stage3 또는 Stage4의 책임이다.
