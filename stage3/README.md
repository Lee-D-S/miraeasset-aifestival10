# Stage3

Stage3는 Stage1의 Intent와 Stage2의 검색·rerank 결과를 입력으로 받아 공시 근거를 구조화하고, 결정론적 계산·비교와 HyperCLOVA X 답변 작성을 수행하는 모듈이다.

현재 구현 단계에서는 Stage1 Intent 입력 계약부터 구현한다. Stage2의 최종 문서 형식은 확정 전까지 별도 어댑터 뒤에 둔다.

## Stage1 입력

```python
from stage3.adapters.stage1 import adapt_stage1_intent

intent = adapt_stage1_intent(stage1_intent_dict, question=question)
```

`route`가 `ok`가 아닌 경우 Stage3는 검색·계산·답변 생성을 진행하지 않고 한계 또는 차단 결과를 반환해야 한다.

Stage3는 `raw_question`, `normalized_question`, `intent`, `route`, `corps`,
`sector`, `sector_members`, `metric`, `metric_confidence`, `basis`, `time`,
`correction_mode`, `allow_pdf_html`, `manifest_filter`, `doc_count`,
`availability`, `assumptions`, `warnings`, `missing_slots`, `reject_reason`,
`clarify_message`, `llm_used`를 Stage1 원본과 함께 보존한다. `manifest_filter`는
Stage3가 다시 만들지 않는다. `route`가 `ok`가 아니면 API 경계에서 Stage2
provider를 호출하지 않는다.

Stage1이 정의한 metric key는 `stage3/metric_registry.py`의
`STAGE1_METRICS`에서 관리한다. 이 registry는 질의를 재분류하지 않고, 후속 Fact
추출기가 Stage1 metric에 맞는 공시 필드를 선택할 때 사용한다.

재무 숫자 Fact는 `revenue`, `operating_profit`, `net_income`, `capex`와
`total_assets` 계열을 처리한다. `total_assets` 계열은 원문 라벨에 따라
`assets`, `liabilities`, `equity`, `ratio`로 분리한다. `supply_contract`,
`contract_termination`, `facility_investment`, `fundraising`,
`major_shareholding`은 유형별 field Fact를 추가하고, `business_overview`,
`investment_plan`, `mgmt_judgement`, `rnd`, `dividend`, `employees`,
`shareholders`, `litigation`, `restructuring`, `treasury_stock`은 근거 section
Fact로 저장한다. 모든 Fact는 문서 ID·출처·근거를 함께 가진다.

## Stage2 입력

```python
from stage3.adapters.stage2 import adapt_stage2_bundle

bundle = adapt_stage2_bundle(stage2_result)
documents = bundle.effective_documents()
```

Stage2의 최종 문서 계약이 확정되기 전까지 `adapt_stage2_bundle()`이 필드 차이를 흡수한다. Stage3는 검색·rerank를 수행하지 않고 전달받은 문서와 근거 구간만 사용한다.

문서 목록은 `documents`, `retrieved_documents`, `results`, 인용 목록은
`cited_documents`, `citedDocuments` alias를 지원한다. 문서 ID·본문·출처·점수와
근거 span도 각각 계획된 alias에서 표준 필드로 변환한다. 인용 문서가 있으면
`effective_documents()`가 인용 문서를 우선 반환한다. ID가 없는 문서는 근거로
사용하지 않으며, 본문이 없더라도 텍스트가 있는 evidence span은 보존한다.

중첩 `metadata`를 우선하고 top-level의 `corp_name`, `corp_code`, `doc_group`,
`doc_subtype`, `report_nm`, `rcept_no`, `rcept_dt`, `flr_nm`, `base_year`,
`base_month`, `is_correction`, `file_path`, `file_format`, `n_files`,
`report_period`, `basis`를 누락 시 fallback으로 채운다. 그 외 입력 필드는
어댑터의 `raw`에 남긴다.

## Fact 추출과 정규화

```python
from stage3.agents.fact_extraction import extract_facts
from stage3.deterministic.normalization import normalize_facts

facts = extract_facts(bundle.effective_documents(), intent)
facts, warnings = normalize_facts(facts, intent)
```

추출 결과는 지표·원값·단위·정규화값·기간·연결/별도 기준·기업·문서 ID·근거 구간을 보존한다. 계산에 사용할 값은 `normalized_value`를 사용하며, 기간·기준이 불명확하면 경고를 남긴다.

본문이 DART XML이면 `xml.etree.ElementTree`, HTML이면 `html.parser.HTMLParser`로
표를 읽는다. `<TABLE>/<TR>/<TD>/<TH>/<TU>`와 HTML `table/tr/td/th/span`을
지원하며 `colspan`·`rowspan`, 별도 단위 행, `연결조정 전·후`, `△` 음수 표기를
구조화한다. 금액 표는 KRW 단위를 정규화하고 USD 같은 외화는 `currency`를
분리해 보존하며 환율을 임의로 적용하지 않는다. 표 Fact에는 행·열·단위·기간
context도 남긴다.

Stage2가 PDF 경로만 전달하고 본문 또는 evidence span을 전달하지 않으면 Stage3는
PDF를 직접 파싱하지 않고 `pdf_text_required` 경고와 `insufficient_evidence`
상태를 반환한다.

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

정정공시는 Stage1 `correction_mode`에 따라 원본만, 최신 정정본 우선, 또는 전체
chain으로 처리한다. 정정 chain은 기업·공시 그룹·세부 유형·기준 기간·정정
표시를 기준으로 묶고, 정정 사유·변경 전후 항목이 없으면 `insufficient_evidence`
경고를 남긴다. 계약 체결·해지 연결은 metadata의 원공시 접수번호 또는 원문
식별자를 먼저 사용한다. 이 정보가 없으면 기업·계약명·상대방·금액·계약일을
정확히 비교하며, 부분 문자열만 같은 문서는 연결하지 않는다.

증감률·CAGR은 Intent가 지정한 기간의 Fact만 선택한다. 비교·순위는
`companies` 또는 `sector_members`를 모두 요구하고 동일 기간·기준·통화의 값만
정렬한다. KRW 단위처럼 변환 가능한 단위는 canonical 값으로 맞추지만, 통화가
다르거나 단위·기간·기준이 없으면 계산하지 않는다. 비중과 영업이익률은
Stage1의 주 지표 Fact와 같은 기업·기간의 매출액 Fact를 분자·분모로 사용한다.
계산 결과에는 산식·입력 Fact의 기간·단위·기준·통화·문서 ID를 함께 저장한다.

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

답변 검증기는 Fact의 문서 ID·citation, 계산 입력과 산식을 재검사하고 답변의
숫자를 Fact·계산 결과와 대조한다. 검증에 실패한 HyperCLOVA X 답변은 사용하지
않고 deterministic fallback으로 교체한다. citation의 `evidence`는 연결된 Fact
근거와 Stage2 evidence span을 우선 사용하며, 외부 제출 응답은
`question_id`, `question`, `retrieved_context`, `think_trace`, `answer` 5개
문자열 필드만 반환한다.

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
python -m unittest stage3.tests.test_structured_parsing -v
python -m unittest stage3.tests.test_fact_extraction -v
python -m unittest stage3.tests.test_calculation_comparison -v
python -m unittest stage3.tests.test_event_linker -v
python -m unittest stage3.tests.test_service_and_api -v
python -m unittest stage3.tests.test_api -v
```

## Actual Stage2 boundary

The Stage2 preprocessing output uses `chunk_id` for the chunk identifier and
`text_content` for the chunk body. Chroma document outputs may expose the body
as `page_content`. The Stage3 adapter accepts all of these aliases:

```text
ID: id, doc_id, document_id, chunk_id
Body: text, content, doc, text_content, page_content
Source: source, source_path, file_path
```

Stage2 metadata such as `rcept_no`, `rcept_dt`, `base_year`, `base_month`,
`doc_group`, `doc_subtype`, `is_correction`, `section_name`, `chunk_type`, and
`raw_json_content` is preserved for Fact extraction and citations.

Stage3 requires a stable document or chunk ID and body text (or an evidence
span). A string-only Stage2 context does not contain a stable ID or metadata,
so it is not converted into a document and produces `insufficient_evidence`.
Stage3 never reconstructs IDs from that string and never reruns Stage2 search
or reranking.

The integrated application injects a structured Stage2 provider from outside
the standalone Stage3 package:

```python
from stage3.api import create_app

app = create_app(
    stage1_provider=parse_stage1,
    stage2_provider=retrieve_stage2,
)
```

The provider must return a mapping with `documents` (or a supported alias),
where each usable item contains a stable ID and body text. Stage3 itself does
not import Stage2's database, Chroma, or embedding dependencies. LangGraph is
loaded only by the optional execution boundary described below.

## Execution modes

`create_app()` accepts an optional `execution_mode` argument:

```python
app = create_app(
    stage1_provider=parse_stage1,
    stage2_provider=retrieve_stage2,
    execution_mode="auto",
)
```

The supported modes are `auto`, `langgraph`, and `stdlib`. `auto` uses the
LangGraph workflow when the optional package is installed and otherwise uses
the standard-library runner. The `langgraph` mode requires the dependencies in
`stage3/requirements-langgraph.txt`; `stdlib` runs without external packages.

## Agent workflow

`Stage3Service` is an execution facade. It adapts the Stage1 Intent and the
structured Stage2 result, then runs one of two implementations of the same
workflow. The service does not call Fact extraction, calculation, comparison,
event linking, or answer writing directly.

```text
Stage1 Intent
  -> route gate
  -> Stage2 evidence adapter
  -> fact_extraction_agent
  -> deterministic analysis router
       -> calculation_agent
       -> comparison_agent
       -> event_linker_agent
  -> merge_analysis
  -> answer_agent
  -> validation_agent
       -> submission
       -> fallback_agent -> validation_agent
```

The LangGraph implementation uses `StateGraph`, conditional edges, and
`Send` for independent specialist branches. The stdlib implementation follows
the same node handlers and contracts in a deterministic sequence. Stage1
already supplies the route, metric, period, basis, and question type, so the
Stage3 router does not classify the question again and does not use an LLM to
choose an agent.

The fixed Agent names are `supervisor`, `fact_extractor`, `calculation`,
`comparison`, `event_linker`, `answer`, `validator`, and `fallback`. Each Agent
result is stored as `AgentResult`; the documents and evidence it used are
stored as `Provenance`. Graph transitions are also recorded as
`HandoffRequest` entries. These records are available in the internal
`Stage3Result` and are not added to the external five-field submission
response.

Stage3 does not contain retrieval, vector search, or reranking nodes. The
Stage2 provider remains responsible for search and reranking and must return a
stable document/chunk ID plus text or evidence spans. A string-only search
result is rejected as evidence rather than being reconstructed into a fake
document.

The optional LangGraph dependency is installed separately:

```powershell
python -m pip install -r stage3/requirements-langgraph.txt
```

If that package is unavailable, use `execution_mode="stdlib"` explicitly or
leave the mode as `"auto"` to select the standard-library fallback. If
`execution_mode="langgraph"` is explicitly requested without the package,
Stage3 raises a configuration error instead of silently switching modes.
