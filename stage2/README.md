# Stage2

Stage2는 Stage1 Intent를 받아 공시 근거 문서를 검색하고 Stage3에 넘기는 검색 전용 모듈이다.

현재 canonical fixture 경로는 `legacy/test_data/disclosure_clova_local.json`을
`JsonFixtureRetriever` adapter로 읽는다. query embedding은 `ClovaQueryEmbedding`을
주입하며 provider가 없으면 `embedding_unavailable`로 종료한다. 테스트와 계약 검증에는
`InMemoryRetriever`와 deterministic reranker를 명시적으로 주입한다.

```python
from stage2 import InMemoryRetriever, build_stage2_node

node = build_stage2_node(retriever=InMemoryRetriever([]))
```

노드는 `stage2_result`와 호환용 `documents`만 작성한다. 답변 생성, 계산, context 작성,
계약·정정공시 관계 해석은 Stage3 또는 Stage4의 책임이다.
