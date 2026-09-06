# NCP 컨테이너 배포 가이드 (친절한 버전)

이 문서 하나만 따라 하면 dis-164 공시 Agent를 **네이버클라우드(NCP) 서버 1대에 Docker 컨테이너로**
띄우고, 평가용 `/answer` 엔드포인트를 외부에 노출할 수 있습니다.

- 대상: `RETRIEVER_MODE=local` (단일 컨테이너, 인덱스는 볼륨 마운트) — 대회 라이브 엔드포인트용 기본 구성
- 관련 문서: [`deployment-design.md`](./deployment-design.md)(설계 배경), [`sync-chunk-index.md`](./sync-chunk-index.md)(인덱스 동기화), `../NCP_DEPLOYMENT.md`(환경변수 상세)

---

## 0. 전체 그림

```
인터넷
  │  (ACG 인바운드: TCP 8000 허용)
  ▼
NCP Server (VM, RAM 32GB+ 권장)
  ├─ Docker
  │   └─ 컨테이너  dis164-agent:local
  │        - FastAPI + LangGraph (uvicorn, worker 1)
  │        - e5 임베딩 ONNX 가중치는 이미지에 내장 (런타임 다운로드 없음)
  │        - :8000  →  /health  /ready  /answer
  └─ Block Storage 볼륨 (150GB+)  →  컨테이너에 /app/data/local_db (read-only) 로 마운트
        ├─ chunk_index.db            (~20GB, SQLite 메타)
        └─ chunk_index_chroma/       (~95GB, Chroma persist + HNSW)
```

핵심 원칙 3가지:

1. **인덱스(~115GB)는 이미지에 넣지 않는다.** Block Storage 볼륨에 두고 컨테이너에 마운트.
2. **CLOVA API 키는 Git·이미지·로그에 남기지 않는다.** 서버의 `.env` 파일로만 주입.
3. **평가 기간(09.07~09.30) 내내 살아 있어야 한다.** `restart: unless-stopped` + 부팅 시 자동 기동.

---

## 1. 준비물

| 항목 | 값 / 확인 |
|---|---|
| NCP 계정 | 대회 제공 크레딧 활성화 |
| 인덱스 파일 | `chunk_index.db` + `chunk_index_chroma/` (Google Drive 또는 로컬 데스크톱에 있음) |
| CLOVA Studio | API Key, 사용할 모델(예: `HCX-DASH-002`) |
| 이 저장소 | `git clone` 가능해야 함 (private repo → PAT 또는 배포키) |

---

## 2. NCP 서버 생성

**Console → Server → 서버 생성**

- 이미지: **Ubuntu 22.04** (또는 최신 LTS)
- 서버 타입: vCPU 8 / **RAM 32GB 이상** 권장
  - 이유: `chunk_index_chroma/`의 HNSW 파일(~22GB)을 mmap 하므로 RAM이 검색 응답 속도를 좌우한다. RAM이 부족하면 스왑 → 지연 폭증.
- 스토리지: 기본(OS) 디스크는 50GB면 충분. **인덱스는 아래 3번의 별도 볼륨에.**
- 공인 IP: 할당
- **ACG(Access Control Group) 인바운드 규칙 추가**
  - `TCP 22` (SSH) — 내 IP만
  - `TCP 8000` — 평가 담당자가 GET 요청을 하는 포트. 주최 측이 특정 IP 대역만 쓴다면 그 대역으로 제한, 아니면 `0.0.0.0/0`

서버가 뜨면 SSH 접속:

```bash
ssh root@<서버_공인_IP>
```

---

## 3. Block Storage 볼륨 붙이기 (인덱스용)

**Console → Server → Storage → 스토리지 생성** → 용량 **150GB+**, 방금 만든 서버에 연결.

서버에서 디스크 초기화 + 마운트:

```bash
# 붙은 디스크 확인 (보통 /dev/xvdb 또는 /dev/vdb)
lsblk

# 파일시스템 생성 (새 디스크일 때만!)
mkfs.ext4 /dev/xvdb

# 마운트
mkdir -p /data
mount /dev/xvdb /data

# 재부팅 후에도 유지되도록 fstab 등록
echo "/dev/xvdb /data ext4 defaults,nofail 0 2" >> /etc/fstab

df -h /data   # 150GB 정도 보이면 성공
```

이제 인덱스가 들어갈 자리는 **`/data/local_db/`** 입니다.

---

## 4. 인덱스 데이터 서버로 옮기기

`/data/local_db/` 아래에 이 두 개가 있어야 합니다:

```
/data/local_db/chunk_index.db
/data/local_db/chunk_index_chroma/
```

### 방법 A — Google Drive에서 rclone (권장, 중단돼도 이어받기)

```bash
# rclone 설치
curl https://rclone.org/install.sh | bash

# 대화형 설정: n(new) → 이름 gdrive → Google Drive 선택 → 브라우저 인증
rclone config

# 받기 (-P 로 진행률 표시, 끊겨도 다시 실행하면 이어받음)
mkdir -p /data/local_db
rclone copy "gdrive:transfer/chunk_index_chroma" /data/local_db/chunk_index_chroma -P
rclone copy "gdrive:transfer/chunk_index.db"     /data/local_db/ -P
```

### 방법 B — 데스크톱에서 rsync (데스크톱에 원본이 있을 때)

```bash
# 데스크톱(원본 보유)에서 실행
rsync -avP --partial \
  ~/contest/team-feature2/data/local_db/chunk_index.db \
  ~/contest/team-feature2/data/local_db/chunk_index_chroma \
  root@<서버_공인_IP>:/data/local_db/
```

### 옮긴 뒤 반드시 검증

```bash
# 크기 (sparse 구멍 없이 다 받아졌는지)
du -sh /data/local_db/chunk_index.db /data/local_db/chunk_index_chroma
#  기대: chunk_index.db ~20GB,  chunk_index_chroma ~95GB

# SQLite 무결성 (20GB라 몇 분 걸림)
apt-get update && apt-get install -y sqlite3
sqlite3 /data/local_db/chunk_index.db "PRAGMA quick_check; SELECT count(*) FROM chunk_index;"
#  기대: ok,  그리고 row 수가 나오면 정상
```

> 압축본(.tar 등)으로 받았다면 풀기 전에 **SHA-256 체크섬을 먼저 대조**하세요.

---

## 5. 저장소 클론 + `.env` 작성

```bash
apt-get install -y git
git clone https://github.com/miraeasset-aifestival-2026-dart/dis-164.git
cd dis-164
```

`.env` 파일 생성 (`.env.example` 복사 후 수정):

```bash
cp .env.example .env
nano .env
```

`.env` 에서 최소한 이 값들을 채웁니다:

```env
CLOVA_API_KEY=<실제 키>
CLOVA_API_HOST=clovastudio.stream.ntruss.com
CLOVA_LLM_ENABLED=true
CLOVA_CHAT_MODEL=HCX-DASH-002
INTERPRETER_USE_LLM=0
QUERY_PLANNER_LLM_ENABLED=false
CLOVA_RERANKER_ENABLED=false
CLOVA_RERANKER_CANDIDATE_LIMIT=100

RETRIEVER_MODE=local
RETRIEVER_EMBEDDING=e5
RETRIEVER_ALLOW_PARTIAL_INDEX=true
```

> `.env` 는 `.gitignore` 에 있어 커밋되지 않습니다. **키를 커밋하지 마세요.**
> 경로(`RETRIEVER_INDEX_PATH` 등)는 아래 compose 파일이 컨테이너 기준으로 이미 넣어주므로 `.env`에서 비워둬도 됩니다.

---

## 6. Docker 설치 + 컨테이너 실행

### Docker 설치

```bash
curl -fsSL https://get.docker.com | sh
docker --version
docker compose version
```

### 이미지 빌드 + 실행

`docker-compose.yml` 이 이미 `local` 모드로 준비돼 있습니다. `INDEX_DIR` 로 인덱스 위치만 알려주면 됩니다.

```bash
cd ~/dis-164

# 빌드 (e5 ONNX 모델 다운로드 포함 -> 첫 빌드는 5~10분)
INDEX_DIR=/data/local_db docker compose build

# 실행 (백그라운드)
INDEX_DIR=/data/local_db docker compose up -d

# 로그 보기
docker compose logs -f
```

`docker-compose.yml` 이 하는 일:

- 이미지 `dis164-agent:local` 실행, 포트 `8000:8000`
- `/data/local_db` → 컨테이너 `/app/data/local_db` **읽기 전용** 마운트
- `RETRIEVER_MODE=local`, `RETRIEVER_EMBEDDING=e5`, `RETRIEVER_CHROMA_COLLECTION=chunk_vectors`, `RETRIEVER_SQL_TABLE=chunk_index` 주입
- `.env` 의 `CLOVA_*` 를 그대로 전달
- `CLOVA_RERANKER_ENABLED=true`일 때만 production Reranker를 활성화하며, 실패 시 deterministic 검색 결과로 fallback
- Docker runtime의 `HF_HUB_OFFLINE=1`로 E5 모델의 런타임 다운로드를 차단
- `restart: unless-stopped` — 컨테이너가 죽거나 서버가 재부팅돼도 자동 재기동

---

## 7. 동작 확인 (스모크)

컨테이너 기동 직후에는 **HNSW 인덱스 로드 때문에 `/ready` 가 잠시 503**일 수 있습니다(수십 초~수 분). 로그에 파이프라인 준비 완료가 뜬 뒤 확인하세요.

```bash
# 1) 프로세스 살아있나
curl -s http://localhost:8000/health
#  {"status":"ok","implementation":"four-stage"}

# 2) 인덱스+provider 준비됐나
curl -s http://localhost:8000/ready
#  {"status":"ready",...}   (503 이면 로그 확인)

# 3) 실제 질의 (5개 필드가 다 오는지)
curl -s "http://localhost:8000/answer?question_id=SMOKE-1&question=삼성전자의 2025년 연결기준 매출액은 얼마인가?" | python3 -m json.tool
#  question_id / question / retrieved_context / think_trace / answer

# 저장소에 포함된 스모크 스크립트로도 가능
docker compose exec app python scripts/smoke_api.py --base-url http://127.0.0.1:8000
```

외부에서도 되는지(평가자 시점):

```bash
curl -s "http://<서버_공인_IP>:8000/answer?question_id=SMOKE-2&question=..." | python3 -m json.tool
```

여기까지 되면 **제출용 Endpoint URL = `http://<서버_공인_IP>:8000/answer`** 입니다. 이 URL을 API 명세서에 적습니다.

---

## 8. 평가 기간(09.07~09.30) 무중단 유지

1. **자동 재기동**: `restart: unless-stopped` 는 이미 설정됨. 서버 재부팅 후에도 뜨도록 Docker 서비스 자동 시작 확인:
   ```bash
   systemctl enable docker
   ```
2. **모니터링**: 최소한 이 정도는 주기적으로 확인
   ```bash
   docker compose ps           # 컨테이너 Up 인지
   curl -sf http://localhost:8000/ready || echo "READY 실패!"
   docker stats --no-stream    # 메모리 여유
   df -h /data                 # 디스크 여유
   ```
   원한다면 cron + 슬랙/메일 알림을 걸어두세요.
3. **크레딧**: NCP 크레딧 소진 시 서버가 정지될 수 있습니다. Console에서 사용량을 주기적으로 확인. 인스턴스 크기를 필요 이상으로 키우지 말 것.
4. **코드 프리즈**: 09.06 마감 이후에는 `git pull` / 재배포 금지(규정상 실격). 이 시점의 이미지·컨테이너를 그대로 유지합니다.

---

## 9. 트러블슈팅

| 증상 | 원인 / 조치 |
|---|---|
| `/ready` 가 계속 503 | 로그 확인(`docker compose logs app`). 대개 (a) 인덱스 경로/마운트 문제 (b) `chunk_index.db` 손상 (c) CLOVA 키 누락. `docker compose exec app python scripts/check_deployment.py` 로 원인 출력 |
| `rust/sqlite/src/db.rs ... panicked` | chromadb 버전 문제. 이미지가 `chromadb==1.5.9` 로 빌드됐는지 확인(`requirements.txt`). 다른 버전이면 재빌드 |
| 검색 결과가 엉뚱함 / 근거 없음 | 임베딩 모델 불일치. `RETRIEVER_EMBEDDING=e5` 인지, 이미지가 `intfloat/multilingual-e5-large`(non-instruct)를 캐시했는지 확인 |
| 컨테이너가 OOM 으로 죽음 | RAM 부족. 서버 타입을 RAM 큰 것으로 (32GB→64GB). worker는 1 유지 |
| 첫 요청이 아주 느림 | HNSW/모델 예열. 기동 후 스모크 질의 1~2회로 예열한 뒤 평가 트래픽을 받게 함 |
| 외부에서 접속 안 됨 | NCP **ACG 인바운드에 TCP 8000** 규칙이 있는지, 컨테이너가 `0.0.0.0:8000` 바인딩인지(`docker compose ps` 포트 표시) 확인 |
| `docker compose build` 가 모델 다운로드에서 멈춤 | 네트워크. `--build-arg SKIP_MODEL_DOWNLOAD=1` 로 빌드 후, 모델을 수동으로 볼륨에 넣고 `FASTEMBED_CACHE_DIR` 지정하는 방법도 있음(아래 참고) |

### 모델을 이미지에 안 굽고 볼륨으로 주는 대안

```bash
# 호스트에서 한 번 받아두기
pip install fastembed
python -c "from fastembed import TextEmbedding; TextEmbedding('intfloat/multilingual-e5-large', cache_dir='/data/models/fastembed')"
```
그리고 compose 에 `- /data/models/fastembed:/opt/models/fastembed:ro` 볼륨을 추가하면 이미지 빌드 시 다운로드를 건너뛸 수 있습니다(`SKIP_MODEL_DOWNLOAD=1`).

---

## 10. 최종 체크리스트

- [ ] NCP 서버 생성, 공인 IP, ACG 인바운드 `TCP 8000`
- [ ] Block Storage 150GB `/data` 마운트, `/etc/fstab` 등록
- [ ] `/data/local_db/chunk_index.db` + `chunk_index_chroma/` 배치 + `du -sh` / `PRAGMA quick_check` 검증
- [ ] 저장소 클론, `.env` 에 `CLOVA_API_KEY` 등 입력 (커밋 안 함)
- [ ] `INDEX_DIR=/data/local_db docker compose up -d --build`
- [ ] `/health` `/ready` `/answer` 스모크 (로컬 + 외부 IP)
- [ ] 예열 질의 1~2회
- [ ] `restart: unless-stopped` + `systemctl enable docker` 확인
- [ ] Endpoint URL `http://<IP>:8000/answer` 를 API 명세서에 기재
- [ ] 09.06 이후 재배포 금지, 평가 기간 모니터링
