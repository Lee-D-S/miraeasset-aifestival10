# RAG 개념과 현재 구현 정리

## 1. RAG란?

RAG(Retrieval-Augmented Generation)는 질문에 답하기 전에 관련 문서를 검색하고, 검색된 문서를 LLM에 근거로 제공하여 답변을 생성하는 구조다.

기본 흐름은 다음과 같다.

```text
문서 수집
→ 문서 분할
→ 문단 또는 chunk 벡터화
→ Vector Store 저장
→ 사용자 질문 벡터화
→ 유사 문서 검색
→ 검색 문서를 LLM에 전달
→ 근거 기반 답변 생성
```

RAG의 핵심 목적은 LLM이 학습한 일반 지식이나 추측만으로 답하지 않고, 지정된 문서의 내용을 근거로 답하게 하는 것이다.

## 2. Chunk와 색인

### Chunk

Chunk는 문서를 검색하기 좋은 단위로 나눈 텍스트 조각이다. 현재 프로젝트에서는 CLOVA 문단 나누기 API가 만든 의미 단위 문단을 기본 chunk로 사용한다.

```text
공시 문서
→ 의미 단위 문단 분할
→ 문단 하나 = chunk 하나
→ chunk 하나당 embedding 벡터 하나
```

단어 하나씩 쪼개는 방식은 문맥이 끊길 수 있으므로 사용하지 않는다. 너무 긴 문단은 추가 분할하고, 너무 짧은 문단은 주변 문단과 합치는 방식이 적합하다.

### 색인

RAG에서 색인은 단순히 벡터를 저장하는 작업만 의미하지 않는다. 다음 과정을 통틀어 색인이라고 한다.

```text
원본 문서 수집
→ 문서 정규화
→ chunk 분할
→ 각 chunk Embedding v2 생성
→ 원문·벡터·메타데이터 저장
```

저장되는 예시는 다음과 같다.

```text
chunk_id: periodic_20230515002335#chunk-7
text: 삼성전자의 주요 제품과 매출 내용...
embedding: 1024개의 숫자
metadata:
  corp_name: 삼성전자
  report_period: 2023-03
  document_type: 분기보고서
  source_path: 원본 XML 경로
  chunk_index: 7
```

## 3. Embedding과 Vector Store

문서 chunk를 Embedding v2에 보내면 1024개의 숫자로 구성된 벡터가 반환된다.

```text
문서 chunk → Embedding v2 → [0.1, -0.2, ...]
```

사용자 질문도 같은 Embedding v2로 벡터화한다.

```text
질문 → Embedding v2 → 질문 벡터
```

그 다음 질문 벡터와 저장된 문서 벡터 사이의 cosine similarity를 계산해 가까운 chunk를 찾는다.

중요한 점은 벡터를 다시 단어로 복원하지 않는다는 것이다. 벡터와 함께 저장해둔 원문 chunk를 ID로 찾아 반환한다.

```text
질문 벡터
→ 저장된 벡터와 유사도 비교
→ 관련 chunk ID 확인
→ 해당 ID의 원문과 메타데이터 조회
```

현재는 PostgreSQL 대신 다음 로컬 Vector Store를 사용한다.

```text
C:\projects\dis-164\test_data\disclosure_clova_local.json
```

나중에 PostgreSQL + pgvector를 사용하더라도 저장 단위는 동일하다.

```text
document_chunks 테이블
└─ 한 행 = chunk 원문 + 1024차원 벡터 + 메타데이터
```

## 4. 일반 RAG와 Agent형 RAG

### 일반적인 RAG

일반적인 RAG는 서버가 검색을 먼저 수행한다.

```text
질문
→ 질문 Embedding
→ Vector Store 검색
→ 관련 원문 추출
→ LLM에 질문과 원문 전달
→ 최종 답변 생성
```

### Agent형 RAG

Agent형 RAG에서는 LLM이 먼저 질문을 보고 검색 도구가 필요한지 판단한다.

```text
질문 + 검색 도구 설명
→ RAG Reasoning LLM
→ 검색이 필요하면 tool call 생성
→ 우리 서버가 tool 실행
→ 검색 결과를 LLM에 전달
→ 최종 답변 생성
```

## 5. CLOVA Studio RAG Reasoning

RAG Reasoning은 CLOVA Studio에서 제공하는 LLM 기반 RAG 추론 모델이다. Vector DB가 아니며, 문서를 직접 저장하거나 직접 검색하지 않는다.

RAG Reasoning의 역할은 다음과 같다.

1. 사용자의 질문을 해석한다.
2. 검색이 필요한지 판단한다.
3. 필요한 경우 검색 도구와 검색어를 결정한다.
4. 검색 결과를 받은 뒤 원문을 읽는다.
5. 원문을 근거로 최종 답변을 작성한다.
6. 답변에 사용한 문서의 인용 표시를 포함할 수 있다.

검색 도구를 실제로 실행하는 것은 우리 서버다.

```text
RAG Reasoning LLM
  └─ “이 질문을 검색해줘”라는 tool call 생성
        ↓
우리 서버
  └─ Embedding 검색 + Vector Store + 리랭커 실행
        ↓
검색 원문과 출처를 RAG Reasoning에 전달
        ↓
RAG Reasoning이 최종 답변 생성
```

## 6. 현재 설정된 검색 도구

현재 RAG Reasoning에 전달하는 검색 도구는 하나다.

```text
ncloud_cs_retrieval
```

도구는 다음과 같이 정의되어 있다.

```json
{
  "name": "ncloud_cs_retrieval",
  "description": "지정된 공시 문서에서 사용자 질문과 관련된 정보를 검색하는 도구입니다.",
  "parameters": {
    "query": "검색어"
  }
}
```

RAG Reasoning이 생성할 수 있는 tool call 예시는 다음과 같다.

```json
{
  "name": "ncloud_cs_retrieval",
  "arguments": {
    "query": "삼성전자 2023년 1분기 주요 사업 매출"
  }
}
```

현재 별도의 `search_by_company_period`나 `search_financial_statement` 도구는 만들지 않았다. 하나의 검색 도구가 내부에서 다음 작업을 모두 수행한다.

```text
검색어 수신
→ 질문 Embedding v2
→ Vector Store 검색
→ 관련 문서 상위 결과 선택
→ CLOVA 리랭커 호출
→ cited_documents 반환
```

## 7. CLOVA RAG Reasoning의 2단계 흐름

### 1차 호출

우리 서버가 질문과 검색 도구 설명을 RAG Reasoning에 전달한다.

```text
질문 + tools
→ RAG Reasoning
→ toolCalls 반환
```

### 서버의 tool 실행

우리 서버가 tool call의 검색어를 사용해 실제 검색을 수행한다.

```text
tool query
→ 질문 Embedding
→ Vector Store 검색
→ 리랭커
→ 검색 원문·메타데이터·출처 생성
```

### 2차 호출

검색 결과를 `tool` 메시지로 RAG Reasoning에 전달한다.

```json
{
  "role": "tool",
  "toolCallId": "call-123",
  "content": {
    "documents": [
      {
        "id": "chunk-7",
        "text": "삼성전자의 2023년 1분기 매출은..."
      }
    ]
  }
}
```

RAG Reasoning은 이 원문을 읽고 문서에 있는 정보만 사용해 최종 답변을 생성한다.

## 8. 현재 코드 구조

```text
C:\projects\dis-164
├─ app.py
├─ requirements.txt
├─ .env.example
├─ README.md
├─ rag/
│  ├─ clients/
│  │  ├─ base.py
│  │  ├─ segmentation.py
│  │  ├─ embedding.py
│  │  ├─ reranker.py
│  │  └─ rag_reasoning.py
│  ├─ generation/
│  │  ├─ answer_generator.py
│  │  └─ tool_schema.py
│  ├─ ingestion/
│  ├─ retrieval/
│  │  ├─ vector_search.py
│  │  └─ rerank.py
│  ├─ services/
│  │  └─ answer_service.py
│  └─ storage/
│     ├─ local.py
│     └─ postgres.py
├─ scripts/
│  ├─ build_clova_local_index.py
│  ├─ search_clova_local_index.py
│  ├─ run_clova_local_rag.py
│  ├─ build_index.py
│  ├─ build_local_index.py
│  ├─ inspect_corpus.py
│  └─ local_smoke_test.py
├─ tests/
│  ├─ test_clova_segmentation.py
│  └─ test_clova_embedding.py
└─ test_data/
   ├─ segmentation_cache.json
   └─ disclosure_clova_local.json
```

## 9. 현재 실행 흐름

### 로컬 RAG end-to-end 테스트

```powershell
cd C:\projects\dis-164

python -m scripts.run_clova_local_rag `
  "삼성전자의 2023년 사업 내용과 주요 제품 매출을 정리해줘" `
  --retrieval-top-k 5 `
  --rerank-top-k 5
```

### 대회용 `/answer`

```text
GET /answer?question_id=Q-001&question=질문내용
```

응답 형식은 대회 명세에 맞춘다.

```json
{
  "question_id": "Q-001",
  "question": "질문내용",
  "retrieved_context": "검색된 공시 근거",
  "think_trace": "검색 및 처리 요약",
  "answer": "최종 답변"
}
```

`POSTGRES_DSN`이 있으면 PostgreSQL을 사용하고, 없으면 `LOCAL_VECTOR_INDEX`에 설정된 로컬 색인을 사용한다.

## 10. 현재 구현 범위

현재 샘플 범위에서는 다음이 동작한다.

```text
질문
→ 질문 Embedding v2
→ 로컬 Vector Store 검색
→ CLOVA 리랭커
→ RAG Reasoning tool call
→ 검색 결과 전달
→ 최종 답변과 인용 문서 반환
```

현재 로컬 색인에는 삼성전자 공시 5개와 총 21개 chunk가 들어 있다. 따라서 전체 대회 데이터에 대한 완성본은 아니며, 샘플 문서 기준의 수직 검증이 완료된 상태다.

검색 결과가 없거나 리랭커가 관련 문서를 선택하지 못하면 근거 없는 답변을 만들지 않고 다음과 같이 반환한다.

```text
제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다.
```

## 11. 다음 구현 단계

1. 전체 공시 문서를 재시작 가능한 방식으로 색인한다.
2. 문서 전체를 처리하고 실패 문서 목록을 기록한다.
3. 기업명·보고 기간·공시 유형 메타데이터 필터를 추가한다.
4. 목차·표·재무제표 chunk의 품질을 점검한다.
5. 여러 기업·기간·질문 유형으로 평가셋을 만든다.
6. 검색 결과와 최종 답변의 숫자·기간·출처를 검증한다.
7. PostgreSQL + pgvector 또는 운영용 Vector DB로 교체한다.
8. 서버 배포 후 대회 평가 API 계약을 최종 점검한다.

