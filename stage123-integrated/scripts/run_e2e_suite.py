from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.composition import Stage123Application


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _configure_runtime() -> None:
    """Load the workspace environment and make Windows JSON output UTF-8."""

    load_dotenv(PROJECT_ROOT / ".env")
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def _test_mode() -> str:
    has_clova_key = bool(
        os.getenv("CLOVA_API_KEY", "").strip()
        or os.getenv("CLOVASTUDIO_API_KEY", "").strip()
    )
    if has_clova_key:
        return "actual_clova_api"
    return "fixture_only (CLOVA key absent; provider cases fail closed)"


def _document_ids(stage2_result: dict[str, object]) -> list[str]:
    ids: list[str] = []
    for document in stage2_result.get("documents", []) or []:
        if not isinstance(document, dict):
            continue
        value = document.get("id") or document.get("document_id") or document.get("chunk_id")
        if value is not None:
            ids.append(str(value))
    return ids


def _failure_classification(run) -> str | None:
    known_successes = {"success", "ok", "need_clarify", "unanswerable", "unsafe", "insufficient_evidence"}
    if run.status in known_successes:
        return None
    for item in run.stage2_result.get("retrieval_trace", []) or []:
        if isinstance(item, str) and item.startswith("failure_class="):
            return item.split("=", 1)[1]
    return run.status


CASES = (
    ("related-samsung-lookup", "삼성전자의 2023년 1분기 매출액은 얼마인가?"),
    ("related-samsung-text", "삼성전자의 2023년 1분기 주요 사업 내용은 무엇인가?"),
    ("unrelated-hyundai", "현대자동차의 2023년 1분기 매출액은 얼마인가?"),
    ("local-not-found", "삼성전자의 2025년 매출액은 얼마인가?"),
    ("local-unsafe", "삼성전자 지금 사도 되나?"),
    ("local-clarify", "2023년 매출액은 얼마야?"),
)


def main() -> int:
    _configure_runtime()
    application = Stage123Application.from_environment()
    backend = os.getenv("E2E_DB_BACKEND", "json").strip().lower()
    embedding_mode = (
        "clova_embedding_api"
        if os.getenv("LOCAL_JSON_USE_CLOVA_EMBEDDING", "0").strip().lower() in {"1", "true", "yes", "on"}
        else "embedding_disabled_or_fixture_only"
    )
    results = []
    for question_id, question in CASES:
        run = application.run(question_id=question_id, question=question)
        results.append(
            {
                "question_id": question_id,
                "question": question,
                "status": run.status,
                "stage1_route": run.intent.get("route"),
                "stage2_status": run.stage2_result.get("status"),
                "document_count": len(run.stage2_result.get("documents", [])),
                "document_ids": _document_ids(run.stage2_result),
                "citation_count": len(run.stage3_result.citations),
                "failure_classification": _failure_classification(run),
                "test_mode": _test_mode(),
                "answer": run.response["answer"],
            }
        )
    print(
        json.dumps(
            {
                "suite": {
                    "data_backend": backend,
                    "data_mode": "json_fixture" if backend == "json" else "production_db",
                    "provider_mode": _test_mode(),
                    "embedding_mode": embedding_mode,
                    "official_entrypoint": "scripts/run_e2e_suite.py",
                },
                "cases": results,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
