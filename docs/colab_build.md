# Colab 에서 `chunk_index` 빌드

`scripts/colab_build_chunk_index.py` — DART 코퍼스(`universe.csv` + `manifest.jsonl` +
`raw/`) → `chunk_index.db`(SQLite) + `chunk_index_chroma/`(Chroma) → zip.
repo 를 clone/import 하지 않는다(파싱·청킹 코드는 `# vendored` 로 인라인).

## 실행 — 셀 2개

파일 안 `===== [셀 1] 끝 =====` 줄에서 잘라 **각각 다른 Colab 셀**에 붙여넣는다.

| 셀 | 하는 일 | 언제 |
|---|---|---|
| **[셀 1]** | pip 설치(bs4·lxml·pdfplumber·chromadb·`sentence-transformers`) → Drive 마운트 → **별도 프로세스**로 torch CUDA + e5 로드 검증(모델 캐시) | 런타임 새로 켤 때마다 1회 |
| **[셀 2]** | 파싱→SQLite · 임베딩→Chroma · zip. **로컬 `/content`** 에서 빌드하고 `OUT_DIR`(Drive)로 주기 스냅샷 | 빌드. 끊기면 **[셀 2]만** 재실행 |

`GPU 런타임`([런타임]>[런타임 유형 변경]>GPU) 필수. [셀 1] 이 `GPU(torch CUDA) 확인 : OK`
를 찍어야 [셀 2] 가 의미 있음.

## 설정 ([셀 1] 상단, 여기만 수정)

| 값 | 기본 | 의미 |
|---|---|---|
| `CORPUS_DIR` | `/content/drive/MyDrive/corpus` | 코퍼스 경로. 대량이면 로컬(`/content`)로 복사 후 지정 — Drive(FUSE) 직접 파싱은 파일 stat 지연으로 매우 느림 |
| `OUT_DIR` | `/content/drive/MyDrive/team-feature2_build` | 재개 스냅샷 보관처(Drive). **빌드 자체는 로컬** `/content/_chunk_index_build` |
| `SELECTION` | `""` | `""`=전체 / `selected_documents.json` 경로 |
| `COLLECTION` | `chunk_vectors` | Chroma 컬렉션명. 서빙 `STAGE2_CHROMA_COLLECTION` 과 일치해야 함 |
| `MAX_CHUNK_LEN` | `1000` | 청크 최대 글자수 |
| `E5_BATCH_SIZE` | `128` | 임베딩 배치. A100 이면 128~256 |
| `FP16` | `True` | GPU half precision (A100 ~2배, 검색 품질 차이 무시 가능) |
| `PARSE_WORKERS` | `0` | 파싱 병렬 프로세스. 0=코어 수 자동 |
| `SNAPSHOT_EVERY` | `300000` | 임베딩 이 chunk 수마다 로컬→Drive `rsync` 스냅샷(재개 지점) |
| `RESUME` | `True` | Drive 스냅샷에서 이어서. `False`=처음부터 |
| `USE_GPU` / `REQUIRE_GPU` | `True` / `True` | GPU 사용 / GPU 확인 실패 시 임베딩 전에 정지(CPU 몇 시간 낭비 방지) |

## 재개 동작

- **파싱**: 문서 단위로 `chunk_index` 테이블에 커밋 + `_build_state` 에 완료 플래그.
  코퍼스/셀렉션/청크길이 지문이 바뀌면 테이블 비우고 새로.
- **임베딩**: `SNAPSHOT_EVERY` chunk 마다 로컬 빌드를 `rsync -a --delete --inplace` 로
  `OUT_DIR` 에 스냅샷. [셀 2] 시작 시 로컬이 비어 있으면 Drive 스냅샷 → 로컬 복원 후
  `col.get` 로 이미 임베딩된 `chunk_id` 스킵.
- 런타임이 죽어도 [셀 2] 재실행 = 마지막 스냅샷부터. 손실 ≤ `SNAPSHOT_EVERY` chunk.

## 트러블슈팅

| 증상 | 원인 | 조치 |
|---|---|---|
| `No module named 'loguru'` | (구버전) `--no-deps` 설치 | 현재 셀은 의존성 포함 설치 — [셀 1] 최신본으로 교체 |
| `Failed to load libcudart.so.NN` / `onnxruntime has no attribute SessionOptions` | onnxruntime-gpu ↔ Colab CUDA 버전 불일치 | 현재 셀은 onnxruntime 안 씀(sentence-transformers/torch). [셀 1] 최신본으로 교체 |
| `GPU(torch CUDA) 확인 : 실패` | 런타임이 GPU 아님 / 세션 상태 꼬임 | 런타임 유형 GPU 확인 → [세션 다시 시작] → [셀 1] 재실행 |
| 임베딩 `~30 chunk/s` (A100인데) | Chroma 를 Drive(FUSE)에 쓰는 중 — 배치마다 fsync | 현재 셀은 로컬 `/content` 빌드 + 주기 스냅샷. [셀 2] 최신본으로 교체 |
| 파싱 `ETA 수천 분` | `CORPUS_DIR` 가 Drive(FUSE) — 파일 stat 지연 | 코퍼스를 `/content` 로 복사(가능하면 zip 1개로) 후 `CORPUS_DIR` 지정 |
| `chunk rows: 0` | `CORPUS_DIR` / `SELECTION` 오류 | 경로 확인 |

## 산출물로 서빙

```
STAGE2_MODE=local
STAGE2_EMBEDDING=e5
STAGE2_INDEX_PATH=<unzip>/chunk_index.db
STAGE2_CHROMA_PATH=<unzip>/chunk_index_chroma
STAGE2_CHROMA_COLLECTION=<COLLECTION 과 동일>
```

임베딩 공간: 문서 측은 `"passage: "` 프리픽스 + mean pooling + L2 정규화(sentence-transformers).
서빙 쿼리 측은 fastembed e5 `"query: "` — 같은 비대칭 쌍·같은 1024-dim 공간.

## 벤더링 사본 유지

`# vendored` 구역은 `stage2/ingestion/dart/*.py` 손복사본. 원본 파싱·청킹 로직을 바꾸면
같이 고치고 `pytest tests/test_colab_cell.py` 로 두 쪽 결과가 같은지 확인한다.
로컬 빌드는 `scripts/build_chunk_index.py` (`--workers`, `--embedding e5|clova`).
