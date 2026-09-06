# 37개 사업보고서 재인덱싱

## 배경

`fix/dart-xml-stray-angle-brackets` 브랜치는 DART XML 파서 버그를 고친다. 장식용
캡션(`< TV 시장점유율 추이 >` 등)이 XML 이스케이프 없이 들어 있으면 파서가 `<`를
태그 시작으로 읽어 파싱 트리가 깨진다. 그 지점 이후 내용이 대량으로 유실되고,
재무제표(손익계산서) 챕터가 통째로 빠진다.

현재 서버에 올라간 인덱스(`chunk_index.db` + `chunk_index_chroma`)는 이 버그가
있는 파서로 만들었다. 전체 사업보고서 207건 중 37건(약 18%)이 재무제표 결손
상태로 서비스되고 있다. 코드 수정만으로는 이미 만들어진 인덱스가 바뀌지 않으므로,
이 37건을 고친 파서로 다시 인덱싱해서 반영해야 한다.

대상 문서 37건의 `doc_id` 목록: `reindex_work/affected_37_doc_ids.json`.
기업 예: 삼성전자(3개년 전부), KB금융, CJ제일제당, 카카오, 이마트, 한화오션,
한국항공우주, 두산에너빌리티, 두산로보틱스, 신한지주, 우리금융지주, 케이티,
한전기술, JYP, 파마리서치, 한미약품, 알테오젠, HD현대일렉트릭, 두산퓨얼셀,
디앤디파마텍, 시프트업 등.

## 이전 세션 산출물 (OOM으로 중단)

이전 세션은 재인덱싱을 시도하다가 OOM으로 중단됐다. 산출물은
`/mnt/c/Users/user/Desktop/miraeasset/reindex_37docs/` 에 남아 있다.

| 파일 | 상태 |
|---|---|
| `affected_37_doc_ids.json` | 정상. 37개 doc_id 목록. |
| `merge_delta_into_production.py` | 정상. 서버에서 Chroma 델타를 프로덕션에 병합하는 스크립트. |
| `chunk_index_updated.db` (21.7 GB) | 검증 중. 프로덕션 SQLite 사본에 37건을 교체한 것. |
| `chunk_index_chroma_delta/` | 손상. `length.bin` 0바이트, `col.get()` 실패("Error finding id"). 재생성 필요. |

`chunk_index_chroma_delta` 는 손상됐다. 델타 Chroma는 버리고 다시 만든다.

이전 세션의 OOM은 **서버 반영(merge) 단계**에서 났다. 로컬 빌드가 아니라
`merge_delta_into_production.py` 실행 중이다. 아래 "서버 메모리" 참고.

## 이번 세션 작업 환경

- 작업 디렉터리: `/home/user/contest/reindex_work/` (리눅스 `/dev/sdd`, 여유 775 GB)
- `/mnt/c` 는 100% 차 있고 느리다. 원본만 읽고 산출물은 리눅스 디스크에 둔다.
- 원본 코퍼스: `/mnt/c/Users/user/Desktop/miraeasset/data/3.공시/corpus`
  (`manifest.jsonl` 4204줄, `universe.csv`, `raw/` 4902개 파일). 37건의 raw XML은
  모두 존재한다.
- 37건 서브셋 코퍼스: `reindex_work/corpus37/` (raw 110개 파일, 271 MB)
- 유니코드 정규화 문제: `/mnt/c`(Windows/DrvFs)의 한글 폴더명은 NFD(자모 분해)
  형태다. 파이썬이 `manifest.jsonl`의 NFC 이름으로 만든 경로가 ext4에서 매칭되지
  않는다. 두 단계로 처리했다.
  1. 복사: 한글 이름 대신 `rcept_no`(숫자)로 `find` 해서 raw 디렉터리를 복사.
  2. 정규화: `reindex_work/corpus37/` 아래 모든 파일·폴더 이름을
     `unicodedata.normalize("NFC", ...)`로 rename. 이후 37건 전부 경로 해석됨.
- 빌드 venv: `/home/user/contest/team-feature2/.venv`
  (`requirements.txt` + `requirements-langgraph.txt` + `stage2/ingestion/dart/requirements.txt`
  설치 완료. 추가로 `sentence-transformers` 설치.)

## 임베딩 공간 (중요)

프로덕션 인덱스의 문서 벡터는 Colab에서 다음 방식으로 만들었다
(`scripts/colab_build_chunk_index.py`, `docs/colab_build.md`).

- 모델: `intfloat/multilingual-e5-large` (1024-dim), `sentence-transformers`
- 문서 측: `"passage: "` 프리픽스 + **mean pooling** + L2 정규화
  (`normalize_embeddings=True`)
- Chroma 컬렉션: `chunk_vectors`, `metadata={"hnsw:space": "cosine"}`

델타도 같은 방식으로 임베딩해야 같은 벡터 공간에 들어간다. 이번 재생성은
`sentence-transformers` 로 프로덕션 빌드 방식을 그대로 따른다. `fastembed`는
버전에 따라 pooling 방식이 달라(구버전 CLS, 0.6+ mean) 파리티 위험이 있어 쓰지
않는다.

## 재인덱싱 절차

### 1. 델타 빌드 (로컬)

CPU 임베딩은 이 머신에서 초당 1.5건(약 19시간)이라 못 쓴다. 노트북의 RTX 4060
GPU를 쓴다(CUDA torch 필요). `--device cuda` + fp16 = colab 빌드 방식과 동일,
약 25분.

```bash
cd /home/user/contest/team-feature2
.venv/bin/python scripts/reindex_37_delta.py \
  --corpus-dir /home/user/contest/reindex_work/corpus37 \
  --doc-ids   /home/user/contest/reindex_work/affected_37_doc_ids.json \
  --out-dir   /home/user/contest/reindex_work/delta_build \
  --device cuda
```

산출물:
- `delta_build/chunk_index_delta.db` — 37건의 SQL 행 (`write_rows` 사용)
- `delta_build/chunk_index_chroma_delta/` — 37건의 새 벡터 (Chroma persist)

chromadb 1.5.9 주의: `col.get()` 을 필터 없이 10만 행에 쓰면
"too many SQL variables" 로 실패한다. 검증·병합 코드는 `limit`/`offset` 으로
페이지 단위로 읽어야 한다 (`scripts/reindex_37_delta.py`,
`scripts/merge_reindex_delta.py`, `reindex_work/verify_delta.py` 모두 반영됨).

### 2. 검증 (로컬) — 완료 (2026-09-06)

`.venv/bin/python reindex_work/verify_delta.py` → `OK`.

- `chunk_index_chroma_delta` 벡터 수 = **104,130** = `chunk_index_delta.db` 행 수
  = `chunk_index_updated.db` 의 37건 `chunk_id` 수 — 세 집합이 완전히 일치
- 차원 전부 1024, 서로 다른 `doc_id` 37개
- 세그먼트 파일 정상: `data_level0.bin` 440 MB, `length.bin` 416 KB,
  `link_lists.bin` 887 KB (이전 세션 델타는 `length.bin` 0바이트로 손상이었음)
- 손익계산서/영업이익 포함 청크: 델타 전체 1,993개, 삼성전자 2024 문서 내 다수

### 3. `chunk_index_updated.db` 준비

**검증 완료 (2026-09-06). 이전 세션의 파일을 그대로 쓴다.**

`/home/user/contest/reindex_work/chunk_index_updated.db` (21.7 GB):
- `quick_check` = ok
- `total_rows` = 5,645,218 (프로덕션 5,548,951 + 96,267)
- `distinct_docs` = 4,204 (manifest 전체), `distinct_corps` = 70 (프로덕션과 동일)
- 37건 per-doc 청크 수 합계 = **104,130** — 새 파싱 결과와 정확히 일치
- 삼성전자 2024(`periodic_20250311001085`)의 "손익계산서"/"영업이익" 포함 청크
  = **94개** (프로덕션에서는 0개)

즉 SQLite 쪽은 완료. 서버로 이 파일을 전송해 `chunk_index.db` 를 교체하면 된다.

### 4. 서버 메모리 (OOM 대비)

`merge_delta_into_production.py` 는 프로덕션 Chroma 컬렉션(벡터 550만 개)을 열고
`delete` + `upsert` 한다. chromadb가 HNSW 인덱스를 메모리에 올린다. 벡터만
550만 × 1024 × 4바이트 ≈ 22 GB, 그래프까지 25~30 GB. 이전 세션은 여기서 OOM.

서버 실측(2026-09-06): RAM 31 GB 중 25 GB 사용 중(서빙 컨테이너 `dis164-app-1`이
프로덕션 Chroma HNSW를 메모리에 올려둠), 여유 5.6 GB, 스왑 0. `/data` 여유 143 GB.
`/` 여유 4.3 GB뿐. CPU 8코어. compose 프로젝트 `/root/dis-164`.

대응:
1. 병합 동안 서빙 컨테이너 정지(`docker compose -f /root/dis-164/docker-compose.yml stop`)
   → RAM 확보.
2. `/data` 에 스왑 파일 추가(여유 대비).
   ```bash
   fallocate -l 32G /data/swapfile && chmod 600 /data/swapfile
   mkswap /data/swapfile && swapon /data/swapfile
   # 병합 후: swapoff /data/swapfile && rm /data/swapfile
   ```
3. 병합은 이미지 `dis164-agent:local` 로 일회성 컨테이너 실행
   (`merge_delta_into_production_v2.py`: 델타를 먼저 메모리에 읽고 델타 클라이언트를
   닫은 뒤 프로덕션을 연다 — 큰 컬렉션 2개 동시 오픈 방지).

### 백업 (스냅샷 대신)

NCP 볼륨 스냅샷은 용량이 커서 사용하지 않는다. 대신:

- `chunk_index.db` → `/data/local_db/chunk_index.db.bak.<날짜>` (21 GB, 그대로 복사)
- `chunk_index_chroma` → `/data/chunk_index_chroma.bak.<날짜>.tar.zst`
  (zstd 압축, 예상 40~50 GB, `/data` 여유 안에 들어감). 서비스 유지 중에 생성 가능.
  복구:
  ```bash
  rm -rf /data/local_db/chunk_index_chroma
  zstd -dc /data/chunk_index_chroma.bak.<날짜>.tar.zst | tar -C /data/local_db -x
  ```

### 5. 서버 반영

상세 순서는 `reindex_work/DEPLOY_RUNBOOK.md` 참고. 요약:

1. `/data/reindex/` 에 델타 + `chunk_index_updated.db` + `scripts/merge_reindex_delta.py`
   + `affected_37_doc_ids.json` 전송
2. 백업: `chunk_index.db` 복사, `chunk_index_chroma` zstd 압축본
3. 스왑 32 GB 추가, 서빙 컨테이너 정지
4. `dis164-agent:local` 이미지로 일회성 컨테이너에서 `merge_reindex_delta.py` 실행
   (37건 낡은 벡터 삭제 → 델타 104,130개 upsert → 검증)
5. `chunk_index.db` 를 `chunk_index_updated.db` 로 교체
6. 컨테이너 기동, `/answer` 로 삼성전자 2024 영업이익 스모크 테스트
7. 스왑 제거

## 진행 상태

- [x] 이전 산출물 확인, 손상 범위 파악 (델타 Chroma `length.bin` 0바이트 = 손상)
- [x] 작업 디렉터리 구성, 37건 서브셋 코퍼스 staging, NFC 정규화
- [x] 빌드 venv 준비 (langgraph, dart deps, sentence-transformers, CUDA torch)
- [x] 델타 빌드 스크립트 작성 (`scripts/reindex_37_delta.py`)
- [x] `chunk_index_updated.db` 무결성/행수 검증 (ok, 37건 합계 104,130)
- [x] 델타 빌드 실행 (GPU fp16, 약 25분, 104,130 벡터)
- [x] 델타 검증 (`verify_delta.py` → OK, 세 집합 일치)
- [x] 배포 런북 작성 (`reindex_work/DEPLOY_RUNBOOK.md`), 병합 스크립트
  (`scripts/merge_reindex_delta.py`)
- [ ] **서버 반영 및 스모크 테스트 (내일 아침, 사용자 SSH 필요)**
- [ ] 파서 수정 브랜치 PR

## 로컬 산출물 (전송 대기)

`/home/user/contest/reindex_work/`
- `chunk_index_updated.db` (21.7 GB) — 프로덕션 SQLite + 37건 교체본
- `delta_build/chunk_index_chroma_delta/` (1.2 GB) — 37건 새 벡터
- `affected_37_doc_ids.json`, `merge_delta_into_production_v2.py`(=repo의
  `scripts/merge_reindex_delta.py`), `DEPLOY_RUNBOOK.md`, `verify_delta.py`

_최종 업데이트: 2026-09-06_
