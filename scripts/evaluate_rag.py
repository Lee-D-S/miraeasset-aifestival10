"""Evaluate answer grounding and factual fields against curated cases."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

# Support both `python scripts/evaluate_rag.py` and `python -m scripts.evaluate_rag`.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from rag.services.answer_service import AnswerService
from rag.schemas import AnswerResponse


FALLBACK_ANSWER = "제공된 공시 문서에서는 해당 정보를 확인할 수 없습니다."
NUMBER_RE = re.compile(r"\d+(?:[,.]\d+)*")


def _normalize(value: str) -> str:
    return re.sub(r"[\s\u3000\-_/·,:;()\[\]{}]", "", value).lower()


def _contains_all(haystack: str, needles: list[str]) -> bool:
    normalized = _normalize(haystack)
    return all(_normalize(needle) in normalized for needle in needles)


def _value_matches(answer: str, expected_values: list[str]) -> bool:
    if not expected_values:
        return True
    normalized_answer = _normalize(answer)
    for expected in expected_values:
        normalized_expected = _normalize(expected)
        if normalized_expected in normalized_answer:
            continue
        expected_numbers = [number.replace(",", "") for number in NUMBER_RE.findall(expected)]
        if not expected_numbers or not all(number in normalized_answer.replace(",", "") for number in expected_numbers):
            return False
        units = re.findall(r"조|억|만|원|%|주|개", expected)
        if units and not any(unit in answer for unit in units):
            return False
    return True


def _source_names(context: str) -> list[str]:
    return re.findall(r"\[출처:\s*([^\]]+)\]", context)


def evaluate_case(case: dict[str, Any], service: Any) -> dict[str, Any]:
    started = time.perf_counter()
    failure_reasons: list[str] = []
    response: AnswerResponse | None = None
    error: str | None = None
    try:
        response = service.answer(case["question_id"], case["question"])
    except Exception as exc:  # The evaluator must continue through API/DB failures.
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - started) * 1000, 2)

    if error:
        return {
            "question_id": case["question_id"],
            "passed": False,
            "retrieval_hit": False,
            "source_match": False,
            "grounded": False,
            "numeric_match": False,
            "period_match": False,
            "answerable_match": False,
            "answer": "",
            "retrieved_context": "",
            "sources": [],
            "latency_ms": latency_ms,
            "error": error,
            "failure_reasons": ["서비스 호출 오류"],
        }

    context = response.retrieved_context
    answer = response.answer
    sources = _source_names(context)
    answerable = bool(case.get("answerable", True))
    retrieval_hit = bool(context.strip())
    expected_sources = [str(value) for value in case.get("expected_source", [])]
    source_match = (
        all(_contains_all(" ".join(sources), [expected]) for expected in expected_sources)
        if expected_sources
        else (not retrieval_hit if not answerable else retrieval_hit)
    )
    required_terms = [str(value) for value in case.get("required_terms", [])]
    expected_values = [str(value) for value in case.get("expected_values", [])]
    grounded = (
        retrieval_hit
        and _contains_all(answer, required_terms)
        and _value_matches(context, expected_values)
        and not answer.startswith(FALLBACK_ANSWER)
    ) if answerable else answer.startswith(FALLBACK_ANSWER) and not context.strip()
    numeric_match = _value_matches(answer, expected_values)
    period_match = _contains_all(answer, [str(value) for value in case.get("expected_periods", [])])
    answerable_match = (
        bool(answer.strip()) and not answer.startswith(FALLBACK_ANSWER)
        if answerable
        else answer.startswith(FALLBACK_ANSWER) and not context.strip()
    )

    checks = {
        "retrieval_hit": retrieval_hit,
        "source_match": source_match,
        "grounded": grounded,
        "numeric_match": numeric_match,
        "period_match": period_match,
        "answerable_match": answerable_match,
    }
    for name, passed in checks.items():
        if name == "retrieval_hit" and not answerable:
            continue
        if not passed:
            failure_reasons.append(name)

    return {
        "question_id": case["question_id"],
        "passed": not failure_reasons,
        **checks,
        "answer": answer,
        "retrieved_context": "출처: " + ", ".join(sources) if sources else "",
        "context_length": len(context),
        "sources": sources,
        "latency_ms": latency_ms,
        "error": None,
        "failure_reasons": failure_reasons,
    }


def build_markdown(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# RAG 평가 결과",
        "",
        f"- 실행 시각: `{report['generated_at']}`",
        f"- 평가 케이스: {summary['total']}개",
        f"- 통과: {summary['passed']}개 / 실패: {summary['failed']}개",
        f"- 평균 응답 시간: {summary['average_latency_ms']}ms",
        "",
        "| 질문 ID | 결과 | 검색 | 출처 | 근거 | 숫자 | 기간 | 무응답/답변 | 실패 사유 |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in report["cases"]:
        lines.append(
            f"| {item['question_id']} | {'PASS' if item['passed'] else 'FAIL'} | "
            f"{item['retrieval_hit']} | {item['source_match']} | {item['grounded']} | "
            f"{item['numeric_match']} | {item['period_match']} | {item['answerable_match']} | "
            f"{', '.join(item['failure_reasons']) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def run(cases: list[dict[str, Any]], service_factory: Callable[[], Any]) -> dict[str, Any]:
    service = service_factory()
    results = [evaluate_case(case, service) for case in cases]
    total = len(results)
    passed = sum(item["passed"] for item in results)
    average_latency = round(sum(item["latency_ms"] for item in results) / total, 2) if total else 0
    summary = {
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(passed / total, 4) if total else 0,
        "retrieval_hit_rate": round(sum(item["retrieval_hit"] for item in results) / total, 4) if total else 0,
        "source_match_rate": round(sum(item["source_match"] for item in results) / total, 4) if total else 0,
        "grounded_rate": round(sum(item["grounded"] for item in results) / total, 4) if total else 0,
        "numeric_match_rate": round(sum(item["numeric_match"] for item in results) / total, 4) if total else 0,
        "period_match_rate": round(sum(item["period_match"] for item in results) / total, 4) if total else 0,
        "answerable_match_rate": round(sum(item["answerable_match"] for item in results) / total, 4) if total else 0,
        "average_latency_ms": average_latency,
        "error_count": sum(1 for item in results if item.get("error")),
    }
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "cases": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default="tests/eval_cases.json")
    parser.add_argument("--output", default="test_data/evaluation/latest_evaluation.json")
    args = parser.parse_args()

    cases_path = Path(args.cases)
    cases = json.loads(cases_path.read_text(encoding="utf-8"))
    report = run(cases, AnswerService)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    output_path.with_suffix(".md").write_text(build_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
