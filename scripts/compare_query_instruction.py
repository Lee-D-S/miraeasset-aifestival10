"""Compare raw E5 queries with the fixed multilingual-e5-instruct prefix.

This command is deliberately an opt-in supplied-index check. It requires a
pre-existing SQLite/Chroma index and a locally cached E5 model, never
downloads model files, and never opens the supplied index in write mode.
"""

from __future__ import annotations

import argparse
import gc
import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from integration.graph import StageNodes
from integration.readiness import PARTIAL_INDEX_ISSUES, raise_if_invalid
from integration.service import StagePipeline
from integration.testing import DeterministicAnswerWriter, DeterministicSemanticValidator
from integration.api import to_submission_response
from stage1 import build_stage1_node
from stage2 import E5InstructEmbeddings, LocalHybridRetriever, RetrievalConfig, build_stage2_node
from stage2.backends import local_chroma, readonly_sqlite_engine
from stage2.embedding import QUERY_INSTRUCTION
from stage2.query_instruction_eval import (
    GoldQuery,
    compare_summaries,
    load_gold_queries,
    summarize_results,
)
from stage3 import build_stage3_node
from stage4 import build_stage4_node


EXPECTED_CATEGORIES = (
    "single_lookup",
    "multi_year",
    "multi_company",
    "complex_reasoning",
    "information_limit",
)
DEFAULT_GOLD_SET = ROOT / "tests" / "fixtures" / "e5_query_instruction_gold.json"


def _resolve_path(value: str | Path | None, default: Path) -> Path:
    path = Path(value).expanduser() if value else default
    return (ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def _resolve_index_root(value: str | Path | None) -> tuple[Path, Path]:
    root = _resolve_path(value, config.LOCAL_DB_DIR)
    if (root / "chunk_index.db").is_file():
        return root / "chunk_index.db", root / "chunk_index_chroma"
    local = root / "local_db"
    return local / "chunk_index.db", local / "chunk_index_chroma"


def _validate_gold_set(queries: tuple[GoldQuery, ...]) -> None:
    if len(queries) != 25:
        raise ValueError(f"gold set must contain 25 queries, got {len(queries)}")
    counts = Counter(query.category for query in queries)
    for category in EXPECTED_CATEGORIES:
        if counts[category] != 5:
            raise ValueError(
                f"gold set category {category!r} must contain 5 queries, "
                f"got {counts[category]}"
            )
    unknown = sorted(set(counts) - set(EXPECTED_CATEGORIES))
    if unknown:
        raise ValueError(f"gold set contains unknown categories: {unknown}")
    answerable = sum(query.answerable for query in queries)
    if answerable != 20:
        raise ValueError(f"gold set must contain 20 answerable queries, got {answerable}")
    if any(
        query.category == "information_limit" and query.safety_expectation != "fail_closed"
        for query in queries
    ):
        raise ValueError("information_limit queries must require fail_closed")


def _build_retriever(
    db_path: Path,
    chroma_path: Path,
    *,
    query_instruction: str | None,
) -> LocalHybridRetriever:
    if not db_path.is_file():
        raise RuntimeError(f"SQLite index is missing: {db_path}")
    if not chroma_path.is_dir():
        raise RuntimeError(f"Chroma directory is missing: {chroma_path}")
    embedder = E5InstructEmbeddings(
        device="cpu",
        local_files_only=True,
        query_instruction=query_instruction,
    )
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


def _run_retrieval(
    queries: tuple[GoldQuery, ...],
    retriever: LocalHybridRetriever,
    *,
    top_k: int,
) -> dict[str, list[str]]:
    results: dict[str, list[str]] = {}
    for query in queries:
        candidates = retriever.filter_candidates(query.manifest_filter, limit=2_000)
        documents = retriever.vector_search(query.question, candidates, limit=top_k)
        results[query.id] = [
            str(document.get("chunk_id") or document.get("id") or "")
            for document in documents
            if str(document.get("chunk_id") or document.get("id") or "")
        ]
    return results


def _build_pipeline(retriever: LocalHybridRetriever, corpus_dir: Path) -> StagePipeline:
    stage1 = build_stage1_node(
        corpus_dir=corpus_dir,
        config_dir=corpus_dir / "config",
        use_llm=False,
    )
    return StagePipeline(
        StageNodes(
            stage1=stage1,
            stage2=build_stage2_node(
                retriever=retriever,
                config=RetrievalConfig(
                    candidate_limit=1_000,
                    branch_limit=100,
                    final_limit=200,
                ),
            ),
            stage3=build_stage3_node(answer_writer=DeterministicAnswerWriter()),
            stage4=build_stage4_node(
                validator_client=DeterministicSemanticValidator()
            ),
        )
    )


def _safety_result(state: dict[str, Any]) -> dict[str, Any]:
    intent = state.get("intent") or {}
    stage3 = state.get("stage3_result") or {}
    stage4 = state.get("stage4_result") or {}
    numeric = stage4.get("numeric_check") or {}
    semantic = stage4.get("semantic_check") or {}
    numeric_pass = numeric.get("pass") if isinstance(numeric, dict) else None
    unsupported_claims = semantic.get("unsupported_claims", [])
    if not isinstance(unsupported_claims, list):
        unsupported_claims = []
    route = str(state.get("route") or "")
    metric = str(intent.get("metric") or "").strip()
    question_type = str(intent.get("question_type") or "").strip()
    stage3_status = str(stage3.get("status") or "")
    stage4_status = str(stage4.get("status") or "")
    blocked = (
        route != "ok"
        or not metric
        or question_type in {"", "text", "unknown"}
        or stage3_status in {"insufficient_evidence", "error", "empty", "blocked"}
        or stage4_status in {"unanswerable", "unsafe", "validation_failed", "blocked"}
    )
    safe = bool(blocked and numeric_pass is not True and not unsupported_claims)
    return {
        "safe": safe,
        "route": route,
        "metric": metric,
        "question_type": question_type,
        "stage3_status": stage3_status,
        "stage4_status": stage4_status,
        "numeric_pass": numeric_pass,
        "unsupported_claim_count": len(unsupported_claims),
    }


def _run_safety(
    queries: tuple[GoldQuery, ...],
    retriever: LocalHybridRetriever,
    corpus_dir: Path,
) -> dict[str, dict[str, Any]]:
    pipeline = _build_pipeline(retriever, corpus_dir)
    results: dict[str, dict[str, Any]] = {}
    for query in queries:
        state = pipeline.invoke(
            question_id=f"ab-{query.id}",
            question=query.question,
        )
        response = to_submission_response(state)
        if set(response) != {
            "question_id",
            "question",
            "retrieved_context",
            "think_trace",
            "answer",
        } or not all(isinstance(value, str) for value in response.values()):
            raise RuntimeError(f"unexpected five-string response contract: {query.id}")
        results[query.id] = _safety_result(state)
    return results


def _run_condition(
    queries: tuple[GoldQuery, ...],
    db_path: Path,
    chroma_path: Path,
    corpus_dir: Path,
    *,
    query_instruction: str | None,
    top_k: int,
    validate_index: bool = False,
) -> tuple[dict[str, list[str]], dict[str, dict[str, Any]]]:
    retriever = _build_retriever(
        db_path,
        chroma_path,
        query_instruction=query_instruction,
    )
    try:
        if validate_index:
            tolerated = PARTIAL_INDEX_ISSUES if config.allow_partial_index() else frozenset()
            raise_if_invalid(retriever.readiness_issues(), tolerate=tolerated)
            raise_if_invalid(
                retriever.manifest_consistency_issues(corpus_dir / "manifest.jsonl"),
                tolerate=tolerated,
            )
        retrieval = _run_retrieval(queries, retriever, top_k=top_k)
        safety = _run_safety(
            tuple(query for query in queries if not query.answerable),
            retriever,
            corpus_dir,
        )
        return retrieval, safety
    finally:
        retriever.engine.dispose()
        del retriever
        gc.collect()


def run_comparison(
    *,
    index_root: str | Path | None = None,
    corpus_dir: str | Path | None = None,
    gold_path: str | Path | None = None,
    top_k: int = 20,
) -> dict[str, Any]:
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    db_path, chroma_path = _resolve_index_root(
        index_root or os.getenv("REAL_INDEX_ROOT")
    )
    corpus = _resolve_path(
        corpus_dir or os.getenv("REAL_CORPUS_DIR"),
        ROOT / "tests" / "fixtures" / "real_index_corpus",
    )
    if not (corpus / "universe.csv").is_file() or not (corpus / "manifest.jsonl").is_file():
        raise RuntimeError(f"real-index corpus is incomplete: {corpus}")
    queries = load_gold_queries(gold_path or DEFAULT_GOLD_SET)
    _validate_gold_set(queries)

    baseline_results, baseline_safety = _run_condition(
        queries,
        db_path,
        chroma_path,
        corpus,
        query_instruction=None,
        top_k=top_k,
        validate_index=True,
    )
    candidate_results, candidate_safety = _run_condition(
        queries,
        db_path,
        chroma_path,
        corpus,
        query_instruction=QUERY_INSTRUCTION,
        top_k=top_k,
    )
    baseline_summary = summarize_results(queries, baseline_results, top_k=top_k)
    candidate_summary = summarize_results(queries, candidate_results, top_k=top_k)
    comparison = compare_summaries(baseline_summary, candidate_summary)

    baseline_safe_count = sum(item["safe"] for item in baseline_safety.values())
    candidate_safe_count = sum(item["safe"] for item in candidate_safety.values())
    candidate_all_safe = (
        len(candidate_safety) == 5
        and all(item["safe"] for item in candidate_safety.values())
    )
    safety = {
        "query_count": len(candidate_safety),
        "baseline_safe_count": baseline_safe_count,
        "candidate_safe_count": candidate_safe_count,
        "candidate_all_safe": candidate_all_safe,
        "candidate_does_not_regress": candidate_safe_count >= baseline_safe_count,
        "baseline": baseline_safety,
        "candidate": candidate_safety,
    }
    adopt_prefix = bool(
        comparison["adopt_prefix"]
        and candidate_all_safe
        and safety["candidate_does_not_regress"]
    )

    details = []
    for query in queries:
        details.append(
            {
                "id": query.id,
                "category": query.category,
                "answerable": query.answerable,
                "expected_chunk_ids": list(query.expected_chunk_ids),
                "baseline_chunk_ids": baseline_results.get(query.id, []),
                "candidate_chunk_ids": candidate_results.get(query.id, []),
            }
        )

    return {
        "model": E5InstructEmbeddings.MODEL_NAME,
        "instruction": QUERY_INSTRUCTION,
        "top_k": top_k,
        "sqlite_path": str(db_path),
        "chroma_path": str(chroma_path),
        "baseline": baseline_summary,
        "candidate": candidate_summary,
        "comparison": comparison,
        "safety": safety,
        "adopt_prefix": adopt_prefix,
        "decision": "ADOPT_PREFIX" if adopt_prefix else "KEEP_RAW",
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index-root", default=os.getenv("REAL_INDEX_ROOT"))
    parser.add_argument("--corpus-dir", default=os.getenv("REAL_CORPUS_DIR"))
    parser.add_argument("--gold-set", default=str(DEFAULT_GOLD_SET))
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        report = run_comparison(
            index_root=args.index_root,
            corpus_dir=args.corpus_dir,
            gold_path=args.gold_set,
            top_k=args.top_k,
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
