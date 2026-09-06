# chunk_index — Retriever 로컬/컨테이너 인덱스

`RETRIEVER_MODE=local`이 서빙하는 하이브리드 인덱스. SQLite 한 테이블
(`chunk_index`)로 메타데이터 필터 → `chunk_id` 집합 → 그 집합 안에서 Chroma 벡터
검색. `LocalHybridRetriever`가 SQLite와 Chroma를 read-only로 함께 사용한다
(`retriever/backends.py`).

## 데이터 계약 — `retriever.contracts.ChunkRow`

모든 청커의 단일 출력 형식. 소스별 어댑터 없음.

```
id, doc_id, chunk_id, text, source_path,
raw_json_content: str | None,     # 표 청크의 행들을 JSON 문자열로 (본문은 None)
metadata: dict,                   # 나머지 전부. metadata_json 으로 저장
embedding: list[float]            # 선택 (write_rows_with_embeddings 전용)
```

`id == chunk_id`이고 이 값이 Chroma 레코드 id이기도 하다(SQL 행 ↔ 벡터 1:1).

## SQLite `chunk_index` 스키마

`metadata_json`에 메타데이터 전체. WHERE절이 쓰는 필드만 타입 컬럼으로 승격:
`corp_name, sector, doc_group, doc_subtype, base_year, base_month, rcept_dt,
rcept_no, is_correction, report_nm, basis, section_name`. 표 청크는
`raw_json_content` 컬럼에 행 JSON을 따로 보관.

`build_manifest_where_and_params()` (참조 `app/tools/rdb_methods.py` 이식):
- `corp_names` (OR LIKE) / `exclude_corp_names` (NOT LIKE) / `sector` LIKE
- `doc_group[_candidates]`, `doc_subtype[_candidates]` IN
- `base_years`, `base_months` IN · `rcept_from`/`rcept_to` 범위
- `is_correction` = · `report_nm_contains`, `section_name_contains` (OR LIKE)
- 후보 정렬 `COALESCE(rcept_dt,'') DESC, COALESCE(rcept_no,'') DESC, id ASC` (최신 공시 우선)

## Ingestion — `retriever/ingestion/`

| 청커 | 진입점 | 표 처리 | 의존성 |
|---|---|---|---|
| plain | `build_chunk_rows()` | 텍스트로 flatten | 없음 (stdlib) |
| dart | `build_dart_chunk_rows(corpus_dir, *, doc_ids=, selection_path=, max_chunk_len=)` | 행 단위 분할 + `raw_json_content` 보존, `[기업\|보고서\|목차]` 헤더 합성, 청크별 연결/별도 감지 | `retriever/ingestion/dart/requirements.txt` |

`dart/`는 참조 `dart_preprocessing` (`converters`·`parsers`·`chunker`)를 벤더링 +
`rows.py` 진입점. `corpus_dir`에 `universe.csv` + `manifest.jsonl` + `raw/` 필요.
`doc_ids`/`selection_path` 없으면 manifest 전체.

## 임베딩 — `RETRIEVER_EMBEDDING`

| 값 | 모델 | 스택 |
|---|---|---|
| `clova` (기본) | CLOVA Embedding v2 (HTTP) | fixture 티어와 동일 공간 |
| `e5` | `intfloat/multilingual-e5-large` (1024-dim) | fastembed / ONNX. **torch·transformers·Pillow 없음** |

`retriever.embedding.E5Embeddings`: fastembed `embed`(passage) / `query_embed`(query)로
e5 프리픽스 자동, L2 정규화만 추가. 빌드·서빙 동일 클래스 → 같은 벡터 공간.
인덱스는 반드시 서빙과 같은 provider로 빌드. `e5` + `fixture`는 기동 전 차단.
`sentence-transformers`를 쓰지 않는 이유: Colab/배포 이미지의 `transformers →
torchvision → Pillow` 체인이 자주 깨짐(`PIL._Ink`).

## 빌드

**로컬 CLI**
```bash
python scripts/build_chunk_index.py --corpus-dir <corpus> --embedding e5
python scripts/build_chunk_index.py --corpus-dir <corpus> \
  --selection data/local_smoke/selected_documents.json --embedding clova
```

**Colab** — `scripts/colab_build_chunk_index.py` (셀 2개: `[셀 1]` 설치+GPU 확인,
`[셀 2]` 빌드). 실행·설정·재개·트러블슈팅은 **[docs/colab_build.md](colab_build.md)**.
요지: 임베딩은 Colab 의 torch+CUDA(`sentence-transformers`)로, 빌드는 로컬 `/content`
에서 하고 `OUT_DIR`(Drive)로 주기 스냅샷 → 런타임이 끊겨도 `[셀 2]` 재실행으로 재개.

**서빙 env**
```
RETRIEVER_MODE=local
RETRIEVER_EMBEDDING=e5
RETRIEVER_INDEX_PATH=<unzip>/chunk_index.db
RETRIEVER_CHROMA_PATH=<unzip>/chunk_index_chroma
RETRIEVER_CHROMA_COLLECTION=chunk_vectors     # 빌드 시 COLLECTION 과 일치
```

현재 active 경로는 local SQLite·Chroma만 사용한다. PostgreSQL·원격 Chroma로의
replay 또는 이관은 지원하지 않는다.
`readiness_issues()` /
`manifest_consistency_issues()`가 SQL `chunk_id` == Chroma id, manifest doc 집합
일치를 오프라인 검증.

## 서브셋 주의

`selection`으로 일부만 색인하면, 서빙 시 `CORPUS_DIR`을 주는 경우 그 `manifest.jsonl`
doc 집합이 색인 집합과 정확히 일치해야 함(`scripts/build_subset_corpus.py`로 생성).
`CORPUS_DIR` 미지정 시 이 검사는 생략.
