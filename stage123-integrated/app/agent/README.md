# app/agent — 에이전트 그래프 (Stage1 + Stage2)

`app/agent`는 LangGraph 기반 에이전트다. **Stage1(질의 이해)** 이 자연어 질문을 Intent JSON으로
바꾸고, 그 결과에 따라 **Stage2(검색·SQL 질의 + RAG 답변 생성)** 로 넘어가거나 즉시 안내 문구로
끝난다. Stage1 자체의 상세 스펙(슬롯, 라우팅 규칙 등)은 `app/stage1/README.md`를 참고할 것.
이 문서는 **그래프(app/agent) 레벨**의 사용법/입출력 명세만 다룬다.

## 사용법

1. 프로젝트 루트에 `data/3.gongsi/corpus`가 있어야 한다(원본 zip의 "3.공시" 폴더명을 `3.gongsi`로
   변경). `CorpusIndex.load()`가 여기서 `universe.csv`/`manifest.jsonl`을 자동 탐색한다.
2. `pip install -r requirements.txt`
3. `.env`에 `CLOVA_API_KEY`를 채운다(Stage2의 `ChatClovaX` 호출에 필요. `CLOVASTUDIO_API_KEY`는
   레거시 호환용으로만 읽는다. `app/agent/edges.py`도
   모듈 로드 시점에 LLM을 생성하므로 키가 없으면 즉시 에러가 난다).
4. DB 생성(최초 1회 및 원본 데이터가 바뀔 때마다):
   ```bash
   python3 dart_preprocessing/preprocesser.py
   ```
5. 그래프 실행:
   ```bash
   python3 app/agent/agent.py
   ```
   `agent.py` 맨 아래 `astream()`의 `messages` 리스트에 있는 문자열이 실제로 보내는 질문이다.
   이 부분을 바꿔서 테스트하면 된다.
6. 다른 스크립트에서 임포트해서 쓸 경우, `app/agent`가 `sys.path`에 있어야 `from state import ...`
   같은 bare import가 풀린다(`agent.py` 상단에서 `PROJECT_ROOT`를 `sys.path`에 넣어주므로,
   `python3 app/agent/agent.py`처럼 스크립트를 직접 실행하는 방식을 권장).

## 파이프라인 개요

```
START
  └─▶ stage1_understand (query_interpreter)      # Stage1: 질문 → Intent JSON + route
        ├─ route == "ok"           ─▶ chatbot ⇄ tools ─▶ context_organizer
        │                                                   ├─ (관련성 낮음/재시도<2) query_transformer ─▶ tools
        │                                                   └─ (관련성 있음/재시도==2) response_generator
        │                                                        └─(hallucination) 아니오 → response_generator 재시도
        │                                                                          예    → END
        ├─ route == "need_clarify"  ─▶ clarify_node       ─▶ END   (Stage3 미구현 placeholder)
        ├─ route == "unanswerable"  ─▶ unanswerable_node  ─▶ END   (Stage3 미구현 placeholder)
        └─ route == "unsafe"        ─▶ unsafe_node        ─▶ END   (Stage3 미구현 placeholder)
```

- Stage1: `app/agent/nodes/stage1.py` (`query_interpreter`) — 내부적으로 `app/stage1`의
  `build_intent`를 호출한다.
- Stage2: `app/agent/nodes/stage2.py` (`chatbot`, `tool_node`, `context_organizer`,
  `query_transformer`, `response_generator`) — 하이브리드 검색(`dart_hybrid_search_tool`, RDB+VectorDB)
  로 근거를 모으고 RAG로 답을 만든다.
- Stage3 placeholder: `app/agent/nodes/fallback.py` (`clarify_node`, `unanswerable_node`,
  `unsafe_node`) — 아직 미구현. 자세한 내용은 [향후 필요한 작업](#향후-필요한-작업) 참고.
- 분기 로직: `app/agent/edges.py`의 `route_decision`(Stage1→Stage2/placeholder),
  `decide_to_generate`(검색 결과 평가), `check_hallucinations`(생성 답변 검증).

## 입력 명세

그래프를 부를 때(`graph.astream(...)` / `graph.invoke(...)`)의 **외부 입력**은
`input_schema=MessagesState`로 제한되어 있어 아래 형태만 허용된다.

```json
{
  "messages": ["자연어 질문 문자열 1개"]
}
```

- `messages`의 문자열은 LangGraph가 자동으로 `HumanMessage`로 변환한다.
- 그 외 필드(`question`, `intent`, `route`, `context`, `answer`, `retry_num`)는 **외부에서 주지 않는다**.
  전부 그래프 실행 중 `stage1_understand`부터 노드들이 채워나가는 내부 상태(`AgentState`, 아래 표)다.

### 내부 상태 `AgentState` (`app/agent/state.py`)

| 필드 | 타입 | 최초로 채우는 노드 | 설명 |
|---|---|---|---|
| `messages` | `list[BaseMessage]` | 외부 입력 | 대화 이력(MessagesState 표준 reducer로 누적) |
| `question` | `str` | `stage1_understand` | 원문 질문. 없으면 `messages[-1]`에서 추출 |
| `intent` | `dict` | `stage1_understand` | Stage1 Intent.to_dict() 결과 (corps/metric/time/manifest_filter 등) |
| `route` | `str` | `stage1_understand` | `ok` \| `need_clarify` \| `unanswerable` \| `unsafe` |
| `context` | `str` | `context_organizer` | 검색된 근거 문서를 정리한 텍스트 |
| `answer` | `str` | `response_generator` / placeholder 노드 | 최종 답변 |
| `retry_num` | `int` | `query_transformer` | 검색 재시도 횟수(2회 이상이면 강제 답변 생성) |

## 노드별 입출력 명세

| 노드 | 읽는 state 필드 | 반환(갱신) 필드 | 비고 |
|---|---|---|---|
| `stage1_understand` (`query_interpreter`) | `question`(없으면 `messages[-1]`) | `question`, `intent`, `route` | `app.stage1.build_intent` 호출. 모듈 로드 시 `CorpusIndex.load()`로 인덱스 1회 생성 |
| `chatbot` | `intent`, `question` | `messages`(tool_call 포함 AIMessage), `question` | Intent를 시스템 프롬프트에 요약해 LLM에게 tool 호출을 유도 |
| `tools` (`ToolNode`) | `messages`(마지막 tool_call) | `messages`(ToolMessage) | `dart_hybrid_search_tool`, `calculator` |
| `context_organizer` | `messages`(연속된 ToolMessage들) | `context`, `messages` | 검색 결과 없으면 `context = "검색된 공시 데이터가 없습니다."` |
| `query_transformer` | `question`, `context`, `messages`(이전 tool_call args) | `question`, `messages`(재검색 tool_call), `retry_num` | `context_organizer` 판정이 "관련 없음"일 때만 진입 |
| `response_generator` | `question`, `context`, `retry_num` | `answer`, `question`, `messages` | `retry_num>=3`이면 "답변 불가 + 대안 질문 제안" 프롬프트로 전환 |
| `clarify_node` / `unanswerable_node` / `unsafe_node` | `intent.clarify_message` | `answer`, `messages` | **Stage3 미구현 placeholder.** `intent.clarify_message`가 있으면 그대로, 없으면 고정 문구 반환 후 즉시 `END` |

## 출력 명세 (그래프 최종 state)

`route == "ok"`이고 검색이 성공한 경우 최종 state는 대략 다음과 같은 모양이다.

```json
{
  "messages": [ "...HumanMessage/AIMessage/ToolMessage 이력 전체..." ],
  "question": "삼성전자의 2025년 연결기준 매출액은?",
  "intent": {
    "intent": "lookup",
    "route": "ok",
    "corps": [ { "corp_name": "삼성전자", "...": "..." } ],
    "metric": "revenue",
    "basis": "연결",
    "manifest_filter": { "...": "..." },
    "...": "..."
  },
  "route": "ok",
  "context": "...정리된 검색 근거 텍스트(페이지 번호 포함)...",
  "answer": "...근거를 인용한 최종 답변...",
  "retry_num": 0
}
```

`route`가 `need_clarify` / `unanswerable` / `unsafe`인 경우, `chatbot` 이후 단계를 타지 않고
바로 끝나므로 `context`/`retry_num`은 초기값(미사용)이고 `answer`만 채워진다. 예:

```json
{
  "messages": [ "HumanMessage(...)", "AIMessage(answer)" ],
  "question": "삼성 매출 알려줘",
  "intent": {
    "route": "need_clarify",
    "clarify_message": "'삼성'만으로는 기업을 특정할 수 없습니다. 다음 중 어느 기업인지 알려주세요: 삼성E&A, 삼성SDI, 삼성바이오로직스, 삼성생명, 삼성전기, 삼성전자"
  },
  "route": "need_clarify",
  "answer": "'삼성'만으로는 기업을 특정할 수 없습니다. 다음 중 어느 기업인지 알려주세요: 삼성E&A, 삼성SDI, 삼성바이오로직스, 삼성생명, 삼성전기, 삼성전자"
}
```

## 향후 필요한 작업

- **Stage3 실제 구현 (가장 급함).** 지금 `clarify_node`/`unanswerable_node`/`unsafe_node`는
  `app/agent/nodes/fallback.py`의 임시 노드로, Stage1이 만든 `clarify_message`를 그대로
  돌려주고 바로 `END`로 끝난다. 실제로는:
  - `need_clarify`: 사용자의 후속 답변을 받아 다시 Stage1(`stage1_understand`)로 되돌리는 멀티턴
    루프가 필요하다(현재는 단발성 안내로 끝남 — 대화가 이어지지 않음).
  - `unanswerable`/`unsafe`: 정책상 END로 끝나는 게 맞는지, 대안 질문 제안 같은 걸 더 붙일지 결정 필요.
- **`query_interpreter` 예외 처리 없음.** `build_intent`가 예외를 던지면 그래프 노드가 그대로
  죽는다. 이상 입력(빈 문자열, 인코딩 깨짐 등)에 대한 방어가 필요.
- **`stage1_understand` ↔ `edges.route_decision` 통합 테스트 부재.** `app/stage1/tests`는
  Stage1 파이프라인 자체(`build_intent`)만 검증하고, 이번에 새로 연결한
  `nodes/stage1.py` + `edges.route_decision` + 그래프 조건부 엣지 조합은 아직 자동화된 테스트가 없다.
- **`CorpusIndex` 싱글턴 재사용 전략.** `app/agent/nodes/stage1.py`가 모듈 로드 시 1회
  `CorpusIndex.load()`를 실행한다. 코퍼스가 바뀌는 경우(재수집 등) 프로세스 재시작 없이 갱신할
  방법, 그리고 테스트에서 목(mock) 인덱스로 주입할 방법이 필요하다.
- **루트 `readme.md` 구조 갱신.** 폴더 트리에 아직 `app/agent/nodes.py`(단일 파일)로 적혀 있는데,
  실제로는 `app/agent/nodes/`(stage1.py/stage2.py/fallback.py/__init__.py) 패키지로 바뀌었다.
- **기존에 열려 있던 이슈** (루트 `readme.md`의 Plan 항목, 이번 작업과 무관하게 여전히 유효):
  "최근 공시 5개?" 같은 광범위한 질문에서 검색이 문서를 잘 못 찾는 문제.
- **`.env`의 `CLOVA_API_KEY` 필수.** `app/agent/edges.py`가 모듈 임포트 시점에 바로
  `ChatClovaX`를 생성하므로, 키가 없으면 `edges`를 import하는 순간(=`agent.py` 로드 시점) 실패한다.
  단독 스크립트에서 `edges.py`만 따로 import해서 쓰려면 그 전에 `load_dotenv()`를 직접 호출해야 한다.
