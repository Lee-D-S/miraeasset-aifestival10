# 런북: Colab 청크 인덱스 → 로컬 동기화

Colab에서 빌드한 Chroma 벡터 인덱스(`chunk_index_chroma`)를 로컬 `data/local_db/`로 가져올 때의 절차.
2026-09-02 최초 동기화 때 정리.

## DB 구성

- 경로: `data/local_db/chunk_index_chroma/`
- `chroma.sqlite3` — 약 78 GB (`78,181,486,592` bytes)
- 컬렉션 폴더 1개: `de28b1f0-bf91-4652-89b5-395a71d20f73/`
  - `data_level0.bin` (~22 GB, `23,503,564,608`), `header.bin` (100), `index_metadata.pickle` (`327,861,444`), `length.bin` (`22,194,112`), `link_lists.bin` (`47,336,568`)
- 컬렉션명: `chunk_vectors`, 약 **5,548,951 rows**
- 총 ~95 GB

## 사전 조건: chromadb 버전 (중요)

- **반드시 `chromadb==1.5.9`** — Colab 빌드 버전과 일치해야 함.
- 1.0.x 등 낮은 버전으로 열면:
  `thread panicked at rust/sqlite/src/db.rs:157:42: range start index 10 out of range for slice of length 9`
  → sqlite 스키마/마이그레이션을 못 읽는 것. 파일 손상 아님.
- venv 권장:
  ```bash
  python3 -m venv .venv && source .venv/bin/activate
  pip install "chromadb==1.5.9"
  ```

## 동기화 절차

### 1. 델타만 판단 — 전체 95 GB를 다시 받지 말 것

로컬에 기존 `chroma.sqlite3`가 있으면 재사용 가능한지 먼저 확인:

```bash
# 로컬
md5sum data/local_db/chunk_index_chroma/chroma.sqlite3
```
```python
# Colab
!md5sum /content/_chunk_index_build/chunk_index_chroma/chroma.sqlite3
```

- 해시 같음 → `chroma.sqlite3`는 그대로 두고 **컬렉션 폴더만** 전송 (~22 GB)
- 해시 다름 → `chroma.sqlite3`도 전송 대상

### 2. 미완성(부분) 파일 정리

이어받기를 신뢰하지 말고, 받을 대상 폴더에 미완성 파일이 있으면 지우고 새로 받는다:

```bash
cd data/local_db/chunk_index_chroma/
rm -rf de28b1f0-bf91-4652-89b5-395a71d20f73
```

> **sparse(미완성) 판별:** `du -sh <dir>` (실제 디스크 사용량) 값이 `ls -la` 겉보기 크기 합보다 크게 작으면 아직 덜 받은 것. 예: 겉보기 98 GiB인데 `du`가 84 GB면 ~14 GB가 구멍.

### 3-A. croc 전송 (기본)

```bash
# 받기 — 대상의 "상위" 디렉토리에서 실행해야 UUID 폴더가 그 아래 생성됨
cd data/local_db/chunk_index_chroma/
CROC_SECRET='<code>' croc
```
```python
# Colab 보내기 — 폴더만 (UUID 전체 이름 사용)
%env CROC_SECRET=<code>
!croc send /content/_chunk_index_build/chunk_index_chroma/de28b1f0-bf91-4652-89b5-395a71d20f73
```

croc 함정:

| 증상 | 원인 / 대처 |
|---|---|
| `croc does not accept receive codes on the command line` | CLI 인자로 코드 못 넣음. `CROC_SECRET=` 환경변수 사용 (또는 대화식 입력, `--classic`) |
| `room is full` | 좀비 연결이 relay 방 점유. 1~2분 대기 또는 코드 변경 후 양쪽 재시작 |
| `flate: corrupt input before offset N` | **양쪽 croc 바이너리 버전 불일치.** `croc --version` 맞추기. Colab은 `curl \| bash`로 최신이 깔리니 GitHub releases에서 버전 고정 권장 |
| `Sender detected a transfer interruption. Retrying securely...` | 대개 자동 복구. 진행바가 멈춰 있으면 셀 재실행 |
| 이어받기 안 됨 | 이어받기는 코드가 아니라 **받는 쪽 디스크의 부분 파일** 기준. 버전이 다르면 전체 재전송됨. 받는 쪽을 부분 파일이 있는 디렉토리에서 실행해야 함 |

### 3-B. Drive + rclone (대용량 / 불안정망일 때 권장)

```python
# Colab
from google.colab import drive; drive.mount('/content/drive')
!rsync -a --partial /content/_chunk_index_build/chunk_index_chroma /content/drive/MyDrive/transfer/
```
```bash
# 로컬 — rclone copy 는 중단돼도 완전한 이어받기
rclone copy "gdrive:transfer/chunk_index_chroma" data/local_db/chunk_index_chroma/ -P
```

croc를 계속 쓸 거면 큰 파일은 `split -b 5G` 로 쪼개서 보내면 끊겨도 그 조각만 다시 받으면 됨.

### 4. 검증

```bash
du -sh data/local_db/chunk_index_chroma/de28b1f0-bf91-4652-89b5-395a71d20f73   # ~22 GB. 축소돼 있으면 미완성
ls -la data/local_db/chunk_index_chroma/de28b1f0-bf91-4652-89b5-395a71d20f73   # 파일 5개 크기가 위 표와 일치하는지
```
```python
import chromadb
c = chromadb.PersistentClient(path="data/local_db/chunk_index_chroma")
for x in c.list_collections():
    col = c.get_collection(x.name)
    print(x.name, col.count())                       # chunk_vectors 5548951
    peek = col.peek(limit=1)
    print(list(peek.keys()))                         # ids/embeddings/documents/metadatas ...
    emb = peek.get("embeddings")
    if emb is not None and len(emb) > 0:
        print("임베딩 차원:", len(emb[0]))
```

- `count()`가 Colab과 일치하고 `peek()`가 에러 없이 임베딩을 반환하면 완료.
- `peek["embeddings"]`는 numpy 배열이므로 `if peek["embeddings"]:` 같은 불리언 평가 금지 (`ValueError: truth value ambiguous`).

## 주의

- 이 DB의 컬렉션은 `chunk_vectors` **하나**뿐. 다른 UUID 폴더(예: `d0382018-...`)가 로컬에 있으면 과거 잔재 → 삭제 가능.
- `data/local_db/` **밖**의 엉뚱한 경로(예: repo 루트 `chunk_index_chroma/`)에 받지 않도록 주의. 앱은 `data/local_db/chunk_index_chroma/` 를 본다.
- Colab 원본에 `chroma.sqlite3-wal` / `-shm` 이 있으면 그것도 함께 가져와야 함 (2026-09-02 시점엔 없었음).
