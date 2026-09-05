"""서버에서 실행: 37개 문서 재인덱싱 델타를 프로덕션 chunk_index_chroma에 반영한다.

v2 변경점 (메모리 안정성):
  - 델타를 먼저 통째로 메모리에 읽고(약 0.4 GB) 델타 클라이언트를 닫는다.
    그 다음에야 프로덕션 컬렉션을 연다. 큰 컬렉션 2개를 동시에 열지 않는다.
  - 단계 사이에 gc.collect().
  - 반영 전 델타 자체를 검증(벡터 수 == 104130, 서로 다른 doc_id 37개, 차원 1024).
  - 반영 후 검증: 프로덕션 개수 = 이전 - 삭제 + 104130,
    삼성전자 2024(periodic_20250311001085)에 "손익계산서" 텍스트가 조회되는지 확인.
  - 재실행 안전(idempotent). 실패 시 그대로 다시 돌리면 된다.

전제:
  - **서빙 컨테이너를 멈춘 상태에서 실행한다** (`docker compose stop`).
    프로덕션 Chroma HNSW 하나만 메모리에 올라가도 ~22 GB다.
  - 스왑을 먼저 켠다 (서버 RAM 31 GB, 여유가 부족).

사전 준비:
  1. 이 파일과 chunk_index_chroma_delta/, affected_37_doc_ids.json 를
     서버 /data/reindex/ 에 올린다 (/ 파티션은 여유 없음, /data 를 쓴다).
  2. 백업: chunk_index_chroma 를 /data 에 zstd 압축본으로 만든다.
       tar -C /data/local_db -c chunk_index_chroma \
         | zstd -3 -T8 -o /data/chunk_index_chroma.bak.$(date +%Y%m%d).tar.zst
     복구가 필요하면:
       rm -rf /data/local_db/chunk_index_chroma
       zstd -dc /data/chunk_index_chroma.bak.YYYYMMDD.tar.zst | tar -C /data/local_db -x
  3. chunk_index.db 교체는 이 스크립트가 하지 않는다. 셸에서:
       cp /data/local_db/chunk_index.db /data/local_db/chunk_index.db.bak.$(date +%Y%m%d)
       mv /data/reindex/chunk_index_updated.db /data/local_db/chunk_index.db

실행 (일회성 컨테이너):
  docker run --rm \
    -v /data/local_db:/data/local_db \
    -v /data/reindex:/reindex \
    dis164-agent:local \
    python3 /reindex/merge_delta_into_production_v2.py \
      --production-chroma /data/local_db/chunk_index_chroma \
      --delta-chroma /reindex/chunk_index_chroma_delta \
      --doc-ids /reindex/affected_37_doc_ids.json \
      --collection chunk_vectors --yes
"""

from __future__ import annotations

import argparse
import gc
import json
import sys
from pathlib import Path

import chromadb
import numpy as np
from chromadb.config import Settings

EXPECTED_VECTORS = 104130
EXPECTED_DOCS = 37
EMBEDDING_DIM = 1024
SAMSUNG_2024 = "periodic_20250311001085"

_FALLBACK_DOC_IDS = [
    "periodic_20240312000736", "periodic_20240313001198", "periodic_20240314001383",
    "periodic_20240314001585", "periodic_20240315000795", "periodic_20240318000392",
    "periodic_20240318000479", "periodic_20240318000635", "periodic_20240319000649",
    "periodic_20240319000861", "periodic_20240320001238", "periodic_20240320001386",
    "periodic_20240320001527", "periodic_20250311001085", "periodic_20250312000998",
    "periodic_20250317000598", "periodic_20250317000648", "periodic_20250317000758",
    "periodic_20250318001067", "periodic_20250318001206", "periodic_20250318001297",
    "periodic_20250319000952", "periodic_20250321000557", "periodic_20250321001817",
    "periodic_20260310002820", "periodic_20260316000940", "periodic_20260316001116",
    "periodic_20260318000826", "periodic_20260318001024", "periodic_20260318001264",
    "periodic_20260318001519", "periodic_20260318001562", "periodic_20260319001417",
    "periodic_20260320001088", "periodic_20260320001130", "periodic_20260320001246",
    "periodic_20260323001596",
]


def _open(persist_dir: str, collection: str):
    client = chromadb.PersistentClient(
        path=persist_dir,
        settings=Settings(anonymized_telemetry=False, migrations="apply"),
    )
    return client, client.get_or_create_collection(collection)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--production-chroma", required=True)
    ap.add_argument("--delta-chroma", required=True)
    ap.add_argument("--collection", default="chunk_vectors")
    ap.add_argument("--doc-ids", type=Path, default=None, help="affected_37_doc_ids.json")
    ap.add_argument("--batch-size", type=int, default=2000)
    ap.add_argument("--yes", action="store_true", help="write without the confirm prompt")
    args = ap.parse_args()

    doc_ids = sorted(
        json.loads(args.doc_ids.read_text(encoding="utf-8")) if args.doc_ids else _FALLBACK_DOC_IDS
    )
    if len(doc_ids) != EXPECTED_DOCS:
        sys.exit(f"expected {EXPECTED_DOCS} doc_ids, got {len(doc_ids)}")

    # ---- 1) read the delta fully (paged), then close it --------------
    print("delta 읽는 중 ...", flush=True)
    d_client, delta = _open(args.delta_chroma, args.collection)
    d_total = delta.count()
    ids: list[str] = []
    metadatas: list[dict] = []
    documents: list[str] = []
    emb_pages: list[np.ndarray] = []
    page = 5000
    for off in range(0, d_total, page):
        g = delta.get(limit=page, offset=off, include=["embeddings", "metadatas", "documents"])
        if not g["ids"]:
            break
        ids.extend(g["ids"])
        metadatas.extend(g["metadatas"])
        documents.extend(g["documents"])
        emb_pages.append(np.asarray(g["embeddings"], dtype=np.float32))
    embeddings = np.vstack(emb_pages) if emb_pages else np.zeros((0, EMBEDDING_DIM), np.float32)
    del emb_pages, delta, d_client
    gc.collect()

    n = len(ids)
    delta_docs = {m.get("doc_id") for m in metadatas}
    print(f"delta: {n} vectors, shape {embeddings.shape}, {len(delta_docs)} docs", flush=True)
    if n != EXPECTED_VECTORS:
        sys.exit(f"delta vector count {n} != expected {EXPECTED_VECTORS}")
    if delta_docs != set(doc_ids):
        sys.exit(f"delta doc_id set mismatch: {sorted(delta_docs)}")
    if embeddings.shape != (n, EMBEDDING_DIM):
        sys.exit(f"delta embedding shape {embeddings.shape} != {(n, EMBEDDING_DIM)}")
    if len(set(ids)) != n:
        sys.exit("duplicate id in delta")

    # ---- 2) open production -----------------------------------------
    print("production 여는 중 (마이그레이션이면 수 분 걸릴 수 있음) ...", flush=True)
    p_client, prod = _open(args.production_chroma, args.collection)
    before = prod.count()
    print(f"production 현재: {before}", flush=True)

    if not args.yes:
        try:
            input("이 상태로 삭제+upsert 를 진행하려면 Enter, 취소는 Ctrl-C > ")
        except EOFError:
            pass

    # ---- 3) delete old vectors for the 37 docs ---------------------
    print(f"기존 37개 문서 벡터 삭제 중 ...", flush=True)
    prod.delete(where={"doc_id": {"$in": doc_ids}})
    gc.collect()
    after_delete = prod.count()
    removed = before - after_delete
    print(f"삭제 후: {after_delete}  (제거 {removed})", flush=True)

    # ---- 4) upsert the delta -------------------------------------
    print("델타 upsert 중 ...", flush=True)
    for start in range(0, n, args.batch_size):
        sl = slice(start, start + args.batch_size)
        prod.upsert(
            ids=ids[sl],
            embeddings=embeddings[sl].tolist(),
            metadatas=metadatas[sl],
            documents=documents[sl],
        )
        done = min(start + args.batch_size, n)
        print(f"  {done}/{n}", flush=True)
        if (start // args.batch_size) % 10 == 0:
            gc.collect()

    final = prod.count()
    expected_final = after_delete + n
    print(f"최종 production: {final}  (기대 {expected_final})", flush=True)

    # ---- 5) verify --------------------------------------------
    probe = prod.get(where={"doc_id": SAMSUNG_2024}, limit=400, include=["documents"])
    hit = sum(
        1 for doc in probe["documents"]
        if doc and ("손익계산서" in doc or "영업이익" in doc)
    )
    print(f"검증: 삼성전자 2024 청크 조회 {len(probe['ids'])}개 중 손익/영업이익 포함 {hit}개", flush=True)

    ok = (final == expected_final) and (hit > 0)
    print("DONE" if ok else "DONE (경고: 검증값 확인 필요)", flush=True)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
