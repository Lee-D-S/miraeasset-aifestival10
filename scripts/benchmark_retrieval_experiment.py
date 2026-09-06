"""Benchmark one retrieval experiment profile on the supplied gold set."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from integration.api import to_submission_response
from integration.graph import StageNodes
from integration.service import StagePipeline
from integration.testing import (
    DeterministicAnswerWriter,
    DeterministicSemanticValidator,
)
from interpreter import build_interpreter_node
from retriever.backends import readonly_sqlite_engine
from retriever.embedding import E5Embeddings
from retriever.node import build_retriever_node
from retriever.query_instruction_eval import (
    GoldQuery,
    load_gold_queries,
    summarize_results,
)
from retriever.retrieval import RetrievalConfig, retrieve
from retriever.retrieval_experiments import (
    EXPERIMENT_PROFILES,
    build_experiment_retriever,
    resolve_index_paths,
)
from reasoner import build_reasoner_node
from validator import build_validator_node


def _rss_bytes() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.Process().memory_info().rss)


def _artifact_size(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def _pipeline_safety_result(state: dict[str, Any]) -> dict[str, Any]:
    intent = state.get("intent") or {}
    reasoner = state.get("reasoner_result") or {}
    validator = state.get("validator_result") or {}
    numeric = validator.get("numeric_check") or {}
    semantic = validator.get("semantic_check") or {}
    numeric_pass = numeric.get("pass") if isinstance(numeric, dict) else None
    unsupported_claims = semantic.get("unsupported_claims", [])
    if not isinstance(unsupported_claims, list):
        unsupported_claims = []
    route = str(state.get("route") or "")
    metric = str(intent.get("metric") or "").strip()
    question_type = str(intent.get("question_type") or "").strip()
    reasoner_status = str(reasoner.get("status") or "")
    validator_status = str(validator.get("status") or "")
    blocked = (
        route != "ok"
        or not metric
        or question_type in {"", "text", "unknown"}
        or reasoner_status in {"insufficient_evidence", "error", "empty", "blocked"}
        or validator_status in {"unanswerable", "unsafe", "validation_failed", "blocked"}
    )
    return {
        "safe": bool(blocked and numeric_pass is not True and not unsupported_claims),
        "route": route,
        "metric": metric,
        "question_type": question_type,
        "reasoner_status": reasoner_status,
        "validator_status": validator_status,
        "numeric_pass": numeric_pass,
        "unsupported_claim_count": len(unsupported_claims),
    }


def _run_pipeline_benchmark(
    *,
    retriever: Any,
    gold_queries: tuple[GoldQuery, ...],
    corpus_dir: str | Path,
) -> dict[str, Any]:
    corpus = Path(corpus_dir).expanduser().resolve()
    interpreter = build_interpreter_node(
        corpus_dir=corpus,
        config_dir=corpus / "config",
        use_llm=False,
    )
    pipeline = StagePipeline(
        StageNodes(
            interpreter=interpreter,
            retriever=build_retriever_node(
                retriever=retriever,
                config=RetrievalConfig(
                    candidate_limit=1000,
                    branch_limit=100,
                    final_limit=200,
                ),
            ),
            reasoner=build_reasoner_node(
                answer_writer=DeterministicAnswerWriter()
            ),
            validator=build_validator_node(
                validator_client=DeterministicSemanticValidator()
            ),
        )
    )
    expected_keys = {
        "question_id",
        "question",
        "retrieved_context",
        "think_trace",
        "answer",
    }
    rows: dict[str, dict[str, Any]] = {}
    for query in gold_queries:
        state = pipeline.invoke(question_id=query.id, question=query.question)
        response = to_submission_response(state)
        safety = _pipeline_safety_result(state)
        validator = state.get("validator_result") or {}
        numeric = validator.get("numeric_check") or {}
        citation = validator.get("citation_check") or {}
        semantic = validator.get("semantic_check") or {}
        rows[query.id] = {
            "response_contract": set(response) == expected_keys
            and all(isinstance(value, str) for value in response.values()),
            "route": str(state.get("route") or ""),
            "retriever_status": str(
                (state.get("retriever_result") or {}).get("status") or ""
            ),
            "reasoner_status": str(
                (state.get("reasoner_result") or {}).get("status") or ""
            ),
            "validator_status": str(validator.get("status") or ""),
            "numeric_pass": numeric.get("pass"),
            "citation_pass": citation.get("pass"),
            "semantic_pass": semantic.get("pass"),
            "safety": safety,
        }
    answerable = [query for query in gold_queries if query.answerable]
    safety_rows = [
        row["safety"]
        for query, row in (
            (query, rows[query.id])
            for query in gold_queries
            if not query.answerable
        )
    ]
    return {
        "query_count": len(gold_queries),
        "response_contract_pass_count": sum(
            bool(row["response_contract"]) for row in rows.values()
        ),
        "answerable_count": len(answerable),
        "answerable_validator_success_count": sum(
            rows[query.id]["validator_status"] in {"success", "regenerated"}
            for query in answerable
        ),
        "fail_closed_count": sum(bool(row["safe"]) for row in safety_rows),
        "information_limit_count": len(safety_rows),
        "by_query": rows,
    }


def _retrieval_ids(result: dict[str, Any]) -> list[str]:
    return [
        str(document.get("chunk_id") or document.get("id") or "")
        for document in result.get("documents", [])
        if str(document.get("chunk_id") or document.get("id") or "")
    ]


def run_benchmark(
    *,
    profile: str,
    index_root: str | Path,
    artifact_root: str | Path,
    gold_queries: tuple[GoldQuery, ...],
    top_k: int,
    nprobe: int,
    pipeline_corpus_dir: str | Path | None = None,
) -> dict[str, Any]:
    db_path, _ = resolve_index_paths(index_root)
    load_started = time.perf_counter()
    embedder = E5Embeddings()
    retriever = build_experiment_retriever(
        profile=profile,
        index_root=index_root,
        artifact_root=artifact_root,
        engine=readonly_sqlite_engine(db_path),
        query_embedder=embedder,
        collection_name=os.getenv("EXPERIMENT_COLLECTION", "chunk_vectors"),
        table_name=os.getenv("EXPERIMENT_TABLE", "chunk_index"),
        nprobe=nprobe,
    )
    load_time = time.perf_counter() - load_started
    issues = retriever.readiness_issues()
    if issues:
        retriever.close()
        raise RuntimeError("; ".join(issues))

    retrieval_results: dict[str, list[str]] = {}
    timings: list[float] = []
    status_by_query: dict[str, str] = {}
    pipeline_report = None
    try:
        for query in gold_queries:
            started = time.perf_counter()
            result = retrieve(
                question_id=query.id,
                question=query.question,
                intent={
                    "normalized_question": query.question,
                    "manifest_filter": query.manifest_filter,
                },
                route="ok",
                retriever=retriever,
                config=RetrievalConfig(
                    candidate_limit=1000,
                    branch_limit=100,
                    final_limit=200,
                ),
            )
            timings.append(time.perf_counter() - started)
            retrieval_results[query.id] = _retrieval_ids(result)[:top_k]
            status_by_query[query.id] = str(result.get("status", ""))
        if pipeline_corpus_dir is not None:
            pipeline_report = _run_pipeline_benchmark(
                retriever=retriever,
                gold_queries=gold_queries,
                corpus_dir=pipeline_corpus_dir,
            )
    finally:
        backend_stats = retriever.backend_stats()
        retriever.close()

    summary = summarize_results(gold_queries, retrieval_results, top_k=top_k)
    ordered = sorted(timings)
    p95_index = min(len(ordered) - 1, int(len(ordered) * 0.95)) if ordered else 0
    return {
        "profile": profile,
        "index_root": str(Path(index_root).expanduser().resolve()),
        "artifact_root": str(Path(artifact_root).expanduser().resolve()),
        "top_k": top_k,
        "nprobe": nprobe,
        "load_seconds": load_time,
        "query_count": len(gold_queries),
        "query_latency_seconds": {
            "min": min(timings) if timings else 0.0,
            "max": max(timings) if timings else 0.0,
            "average": sum(timings) / len(timings) if timings else 0.0,
            "p50": ordered[len(ordered) // 2] if ordered else 0.0,
            "p95": ordered[p95_index] if ordered else 0.0,
        },
        "rss_bytes_after_load": _rss_bytes(),
        "artifact_size_bytes": _artifact_size(Path(artifact_root)),
        "summary": summary,
        "status_by_query": status_by_query,
        "backend_stats": backend_stats,
        "pipeline": pipeline_report,
        "results": retrieval_results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        required=True,
        choices=EXPERIMENT_PROFILES,
    )
    parser.add_argument(
        "--index-root",
        default=os.getenv("EXPERIMENT_INDEX_ROOT", str(config.LOCAL_DB_DIR)),
    )
    parser.add_argument(
        "--artifact-root",
        default=os.getenv(
            "EXPERIMENT_ARTIFACT_ROOT",
            str(config.DATA_DIR / "retrieval_experiments" / "supplied"),
        ),
    )
    parser.add_argument(
        "--gold-set",
        default=str(
            Path(__file__).resolve().parents[1]
            / "tests"
            / "fixtures"
            / "e5_query_instruction_gold.json"
        ),
    )
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--nprobe", type=int, default=32)
    parser.add_argument(
        "--pipeline-corpus-dir",
        help=(
            "also run the same gold queries through deterministic Interpreter to "
            "Validator; requires a corpus with universe.csv and manifest.jsonl"
        ),
    )
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.top_k <= 0 or args.nprobe <= 0:
        parser.error("--top-k and --nprobe must be positive")
    try:
        queries = load_gold_queries(args.gold_set)
        report = run_benchmark(
            profile=args.profile,
            index_root=args.index_root,
            artifact_root=args.artifact_root,
            gold_queries=queries,
            top_k=args.top_k,
            nprobe=args.nprobe,
            pipeline_corpus_dir=args.pipeline_corpus_dir,
        )
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"NOT READY: {type(error).__name__}: {error}")
        return 1
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
