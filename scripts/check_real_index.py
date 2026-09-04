"""Validate the supplied read-only index and run representative local checks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from integration.api import to_submission_response
from integration.graph import StageNodes
from integration.readiness import PARTIAL_INDEX_ISSUES, raise_if_invalid
from integration.service import StagePipeline
from integration.testing import DeterministicAnswerWriter, DeterministicSemanticValidator
from stage1 import build_stage1_node
from stage2 import E5Embeddings, LocalHybridRetriever, RetrievalConfig, build_stage2_node
from stage2.backends import local_chroma, readonly_sqlite_engine
from stage3 import build_stage3_node
from stage4 import build_stage4_node


REPRESENTATIVE_QUERIES = (
    (
        "samsung-2025",
        "삼성전자의 2025년 연결기준 매출액은 얼마인가?",
        {"corp_names": ["삼성전자"], "doc_group": "periodic", "doc_subtype": "annual", "base_years": [2025]},
    ),
    (
        "sk-2025",
        "SK하이닉스의 2025년 연결기준 매출액은 얼마인가?",
        {"corp_names": ["SK하이닉스"], "doc_group": "periodic", "doc_subtype": "annual", "base_years": [2025]},
    ),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sample_hash(path: Path) -> str:
    sample_size = 1024 * 1024
    with path.open("rb") as handle:
        first = handle.read(sample_size)
        handle.seek(max(path.stat().st_size - sample_size, 0))
        last = handle.read(sample_size)
    return hashlib.sha256(first + last).hexdigest()


def _snapshot_tree(root: Path, *, full_hash: bool = False) -> dict[str, tuple[int, str]]:
    """Capture immutable-index state without hashing 18GB on every run.

    Small control files are fully hashed. Large SQLite/vector files use a
    first/last-block hash by default so timestamp-only touches from the Chroma
    client do not look like content writes; ``full_hash`` enables the
    expensive byte-for-byte hash when a release audit requires it.
    """

    return {
        str(path.relative_to(root)): (
            path.stat().st_size,
            _sha256(path)
            if full_hash or path.stat().st_size <= 64 * 1024 * 1024
            else _sample_hash(path),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _resolve_index_root(value: str | Path | None) -> tuple[Path, Path]:
    root = Path(value).expanduser() if value else config.LOCAL_DB_DIR
    root = root if root.is_absolute() else ROOT / root
    root = root.resolve()
    if (root / "chunk_index.db").is_file():
        return root / "chunk_index.db", root / "chunk_index_chroma"
    local = root / "local_db"
    return local / "chunk_index.db", local / "chunk_index_chroma"


def _verify_archive() -> str | None:
    archive = config.DATA_DIR / "team-feature2-local-db.tar.gz"
    checksum = config.DATA_DIR / "team-feature2-local-db.tar.gz.sha256"
    if not archive.is_file() or not checksum.is_file():
        raise RuntimeError("supplied index archive or checksum file is missing")
    expected = checksum.read_text(encoding="utf-8").strip().split()[0].lower()
    actual = _sha256(archive)
    if actual != expected:
        raise RuntimeError("supplied index archive SHA-256 does not match checksum")
    return actual


def _build_retriever(db_path: Path, chroma_path: Path) -> LocalHybridRetriever:
    if not db_path.is_file():
        raise RuntimeError(f"SQLite index is missing: {db_path}")
    if not chroma_path.is_dir():
        raise RuntimeError(f"Chroma directory is missing: {chroma_path}")
    embedder = E5Embeddings()
    return LocalHybridRetriever(
        engine=readonly_sqlite_engine(db_path),
        vectorstore=local_chroma(
            chroma_path,
            embedding_function=embedder,
            collection_name="chunk_vectors",
            create_directory=False,
        ),
        collection_name="chunk_vectors",
        table_name="chunk_index",
        read_only=True,
    )


def _check_pipeline(retriever: LocalHybridRetriever, corpus_dir: Path) -> list[dict[str, Any]]:
    stage1 = build_stage1_node(
        corpus_dir=corpus_dir,
        config_dir=corpus_dir / "config",
        use_llm=False,
    )
    nodes = StageNodes(
        stage1=stage1,
        stage2=build_stage2_node(
            retriever=retriever,
            config=RetrievalConfig(candidate_limit=1000, branch_limit=100, final_limit=200),
        ),
        stage3=build_stage3_node(answer_writer=DeterministicAnswerWriter()),
        stage4=build_stage4_node(validator_client=DeterministicSemanticValidator()),
    )
    pipeline = StagePipeline(nodes)
    scenarios = (
        ("lookup", "삼성전자의 2025년 연결기준 매출액은 얼마인가?"),
        ("comparison", "삼성전자와 SK하이닉스의 2025년 매출액을 비교해줘"),
        ("multi-year", "삼성전자의 2023년과 2025년 매출액을 비교해줘"),
        ("out-of-range", "삼성전자의 2022년 매출액은 얼마인가?"),
        ("insufficient", "삼성전자의 2025년 대표이사 취미는 무엇인가?"),
    )
    results = []
    for question_id, question in scenarios:
        state = pipeline.invoke(question_id=question_id, question=question)
        response = to_submission_response(state)
        if set(response) != {"question_id", "question", "retrieved_context", "think_trace", "answer"}:
            raise RuntimeError(f"unexpected submission response fields for {question_id}")
        if not all(isinstance(value, str) for value in response.values()):
            raise RuntimeError(f"submission response contains non-string fields for {question_id}")
        results.append({
            "id": question_id,
            "route": state.get("route"),
            "stage2_status": (state.get("stage2_result") or {}).get("status"),
            "stage3_status": (state.get("stage3_result") or {}).get("status"),
            "stage4_status": (state.get("stage4_result") or {}).get("status"),
            "answer_length": len(response["answer"]),
        })
    return results


def run_checks(
    *,
    index_root: str | Path | None = None,
    corpus_dir: str | Path | None = None,
    allow_partial_index: bool | None = None,
    verify_archive: bool = False,
    run_pipeline: bool = True,
    full_hash: bool = False,
) -> dict[str, Any]:
    db_path, chroma_path = _resolve_index_root(index_root)
    corpus = Path(corpus_dir) if corpus_dir else ROOT / "tests" / "fixtures" / "real_index_corpus"
    corpus = corpus if corpus.is_absolute() else ROOT / corpus
    corpus = corpus.resolve()
    if not (corpus / "universe.csv").is_file() or not (corpus / "manifest.jsonl").is_file():
        raise RuntimeError(f"real-index corpus is incomplete: {corpus}")

    tolerate = config.allow_partial_index() if allow_partial_index is None else allow_partial_index
    index_root_path = db_path.parent
    before = _snapshot_tree(index_root_path, full_hash=full_hash)
    retriever = _build_retriever(db_path, chroma_path)
    readiness = retriever.readiness_issues()
    raise_if_invalid(readiness, tolerate=PARTIAL_INDEX_ISSUES if tolerate else frozenset())
    with retriever.engine.connect() as connection:
        sqlite_row_count = int(
            connection.exec_driver_sql("SELECT COUNT(*) FROM chunk_index").scalar_one()
        )
    vectorstore = retriever.vectorstore
    collection_count = int(getattr(vectorstore, "collection_count", 0))
    hnsw_count = int(vectorstore._index.get_current_count())
    embedding_dimension = int(getattr(vectorstore, "embedding_dimension", 0))
    if sqlite_row_count <= 0 or collection_count <= 0 or hnsw_count <= 0:
        raise RuntimeError("supplied index contains an empty required component")
    if embedding_dimension != 1024:
        raise RuntimeError(
            f"supplied Chroma dimension is {embedding_dimension}; expected 1024"
        )
    manifest_issues = retriever.manifest_consistency_issues(corpus / "manifest.jsonl")
    raise_if_invalid(manifest_issues, tolerate=PARTIAL_INDEX_ISSUES if tolerate else frozenset())

    searches = []
    for query_id, query, manifest_filter in REPRESENTATIVE_QUERIES:
        candidates = retriever.filter_candidates(manifest_filter, limit=1000)
        result = retriever.vector_search(query, candidates, limit=5)
        if not result:
            raise RuntimeError(f"representative E5 search returned no results: {query_id}")
        searches.append({"id": query_id, "candidate_count": len(candidates), "result_count": len(result)})

    pipeline_results = _check_pipeline(retriever, corpus) if run_pipeline else []
    after = _snapshot_tree(index_root_path, full_hash=full_hash)
    if before != after:
        changed = sorted(
            path
            for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        raise RuntimeError(
            "read-only index files changed during validation: "
            + ", ".join(changed[:12])
        )

    report: dict[str, Any] = {
        "sqlite_path": str(db_path),
        "chroma_path": str(chroma_path),
        "embedding_model": E5Embeddings.MODEL_NAME,
        "sqlite_row_count": sqlite_row_count,
        "chroma_collection_count": collection_count,
        "hnsw_index_count": hnsw_count,
        "embedding_dimension": embedding_dimension,
        "readiness_issues": readiness,
        "manifest_issues": manifest_issues,
        "searches": searches,
        "pipeline": pipeline_results,
        "read_only": True,
    }
    if verify_archive:
        report["archive_sha256"] = _verify_archive()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-root", default=os.getenv("REAL_INDEX_ROOT"))
    parser.add_argument("--corpus-dir", default=os.getenv("REAL_CORPUS_DIR"))
    parser.add_argument("--allow-partial-index", action="store_true")
    parser.add_argument("--verify-archive", action="store_true")
    parser.add_argument("--skip-pipeline", action="store_true")
    parser.add_argument("--full-hash", action="store_true")
    args = parser.parse_args()
    try:
        report = run_checks(
            index_root=args.index_root,
            corpus_dir=args.corpus_dir,
            allow_partial_index=True if args.allow_partial_index else None,
            verify_archive=args.verify_archive,
            run_pipeline=not args.skip_pipeline,
            full_hash=args.full_hash,
        )
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"NOT READY: {type(error).__name__}: {error}")
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
