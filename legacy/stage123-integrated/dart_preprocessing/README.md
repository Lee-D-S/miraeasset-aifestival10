# Production DB 전처리

`dart_preprocessing/`는 공식 JSON fixture E2E가 아니라, 실제 DART corpus를 SQLite와 Chroma로
적재할 때 사용하는 선택적 production setup이다.

## 입력과 실행

입력 corpus 디렉터리에는 다음 파일과 원문 하위 경로가 있어야 한다.

```text
<CORPUS_DIR>/
  universe.csv
  manifest.jsonl
  raw/... 또는 manifest의 file_path가 가리키는 문서
```

workspace 루트에서 `CORPUS_DIR`를 지정해 실행한다.

```powershell
$env:CORPUS_DIR = "C:\path\to\corpus"
python dart_preprocessing/preprocesser.py

# 또는
python dart_preprocessing/preprocesser.py --corpus-dir "C:\path\to\corpus"

# 필요할 때만 파일명 NFC 정규화
python scripts/maintenance/fix_nfd.py --corpus-dir "C:\path\to\corpus"
```

`CORPUS_DIR`가 없거나 `universe.csv`·`manifest.jsonl`이 없으면 실행을 시작하지 않는다. 예전
`data/3.gongsi/corpus` 하드코딩 경로는 사용하지 않는다.

## 임베딩 경계

이 전처리 경로는 `dart_preprocessing/embedding_model.py`의 로컬
`jhgan/ko-sroberta-multitask` HuggingFace 임베딩 모델로 Chroma 문서를 만든다. CLOVA Embedding
API를 호출하는 통합 JSON 검색 경로와 다르다.

| 경로 | 데이터 | 임베딩 |
|---|---|---|
| 공식 기본 E2E | `test_data/disclosure_clova_local.json` | `LOCAL_JSON_USE_CLOVA_EMBEDDING=1`이면 CLOVA API query embedding |
| 선택적 production 전처리 | `CORPUS_DIR`의 DART 원문 | 로컬 HuggingFace `jhgan/ko-sroberta-multitask` |

생성 DB는 `app/config.py`의 `db_tmp/` 아래 SQLite·Chroma 설정을 사용한다. 이 경로는 기본
`scripts/run_e2e.py`가 자동으로 실행하지 않는다.

## 진단 결과

전처리 함수는 `path_not_found`, `no_target_files`, `empty_chunks`, `success_docs`를 반환하고
요약 로그로 출력한다. `success_docs`는 문서가 파싱·청킹된 것만 세지 않고, 해당 배치의 SQLite와
Chroma 적재가 모두 끝난 뒤 증가한다.
