"""Run one or more black-box competition cases and archive their evidence.

The runner deliberately stops at the first failed case by default.  This keeps
the workflow question -> diagnosis -> local fix -> commit -> next question.
Use ``--continue-on-failure`` only for an inventory run.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RESPONSE_FIELDS = {
    "question_id",
    "question",
    "retrieved_context",
    "think_trace",
    "answer",
}


def _request_json(url: str, *, params: dict[str, str] | None = None, timeout: float) -> tuple[int, Any, float]:
    if params:
        url = f"{url}?{urlencode(params)}"
    request = Request(url, headers={"Accept": "application/json"})
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body), (time.perf_counter() - started) * 1000
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            payload: Any = json.loads(body)
        except json.JSONDecodeError:
            payload = {"raw": body}
        return error.code, payload, (time.perf_counter() - started) * 1000
    except (URLError, TimeoutError, OSError) as error:
        return 0, {"error": f"{type(error).__name__}: {error}"}, (time.perf_counter() - started) * 1000


def evaluate_response(case: dict[str, Any], status: int, payload: Any) -> dict[str, Any]:
    """Evaluate deterministic checks declared by a case file."""

    answer = payload.get("answer", "") if isinstance(payload, dict) else ""
    answer_text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    required = [str(item) for item in case.get("required_phrases", [])]
    forbidden = [str(item) for item in case.get("forbidden_phrases", [])]
    required_checks = {phrase: phrase.lower() in answer_text.lower() for phrase in required}
    forbidden_checks = {phrase: phrase.lower() not in answer_text.lower() for phrase in forbidden}
    contract_valid = status == 200 and isinstance(payload, dict) and set(payload) == RESPONSE_FIELDS
    errors = []
    if not contract_valid:
        errors.append(f"HTTP/응답 계약 실패 (status={status})")
    errors.extend(f"기대 문구 누락: {phrase}" for phrase, ok in required_checks.items() if not ok)
    errors.extend(f"금지 문구 포함: {phrase}" for phrase, ok in forbidden_checks.items() if not ok)
    return {
        "pass": not errors,
        "contract_valid": contract_valid,
        "required_checks": required_checks,
        "forbidden_checks": forbidden_checks,
        "errors": errors,
    }


def _json_dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _markdown_response(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _write_case_files(
    case_dir: Path,
    case: dict[str, Any],
    response_record: dict[str, Any],
    judgment: dict[str, Any],
    followups: list[dict[str, Any]],
) -> None:
    response = response_record.get("response", {})
    expected = case.get("expected_answer") or case.get("expected_facts") or case.get("required_phrases", [])
    followup_lines = []
    for item in followups:
        status = "PASS" if item["judgment"]["pass"] else "FAIL"
        followup_lines.append(f"- `{item['case_id']}`: {status} — {item['question']}")
    checks = []
    checks.append(f"- HTTP/응답 계약: {'PASS' if judgment['contract_valid'] else 'FAIL'}")
    checks.extend(f"- 기대 문구 `{phrase}`: {'PASS' if ok else 'FAIL'}" for phrase, ok in judgment["required_checks"].items())
    checks.extend(f"- 금지 문구 `{phrase}`: {'PASS' if ok else 'FAIL'}" for phrase, ok in judgment["forbidden_checks"].items())
    result = "PASS" if judgment["pass"] else "FAIL"
    answer = response.get("answer", "") if isinstance(response, dict) else ""
    result_md = f"""# {case['id']} 테스트 결과

## 질문

{case['question']}

## 기대 답변 및 판정 기준

{_markdown_response(expected) if not isinstance(expected, str) else expected}

## 실제 답변

```text
{answer}
```

## 판정

**{result}**

{chr(10).join(checks)}

## 오류 원인 분석

{'별도 분석 필요: 아래 `원인분석.md`에 기록합니다.' if not judgment['pass'] else '오류 없음.'}

## 꼬리 질문

{chr(10).join(followup_lines) if followup_lines else '없음'}
"""
    (case_dir / "결과.md").write_text(result_md, encoding="utf-8")
    if not judgment["pass"]:
        cause_template = """# 원인 분석

## 직접 원인

<!-- 실제 응답과 기대 답변의 차이를 기록 -->

## 유사 유형 확장 점검

<!-- 같은 원인이 다른 지표·기간·기업·집계 범주에도 나타날 수 있는지 기록 -->

## 꼬리 질문 결과

<!-- 서버에 보낸 추가 질문과 결과를 기록 -->

## 근본 원인 및 수정 방향

<!-- 코드의 공통 원인을 기록 -->

## 로컬 검증

<!-- 실행한 회귀 테스트와 결과를 기록 -->

## 커밋

<!-- 수정사항별 커밋 해시를 기록 -->
"""
        (case_dir / "원인분석.md").write_text(cause_template, encoding="utf-8")


def _run_case(base_url: str, case: dict[str, Any], timeout: float, case_dir: Path) -> dict[str, Any]:
    status, payload, elapsed_ms = _request_json(
        f"{base_url}/answer",
        params={"question_id": str(case["id"]), "question": str(case["question"])},
        timeout=timeout,
    )
    judgment = evaluate_response(case, status, payload)
    record = {
        "case_id": case["id"],
        "question": case["question"],
        "http_status": status,
        "elapsed_ms": round(elapsed_ms, 3),
        "judgment": judgment,
        "response": payload,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    followups = []
    if not judgment["pass"]:
        for followup in case.get("followups", []):
            follow_status, follow_payload, follow_elapsed = _request_json(
                f"{base_url}/answer",
                params={"question_id": str(followup["id"]), "question": str(followup["question"])},
                timeout=timeout,
            )
            follow_judgment = evaluate_response(followup, follow_status, follow_payload)
            follow_record = {
                "case_id": followup["id"],
                "question": followup["question"],
                "http_status": follow_status,
                "elapsed_ms": round(follow_elapsed, 3),
                "judgment": follow_judgment,
                "response": follow_payload,
            }
            followups.append(follow_record)
            _json_dump(case_dir / f"꼬리질문-{followup['id']}-응답.json", follow_record)
    _json_dump(case_dir / "응답.json", record)
    _write_case_files(case_dir, case, record, judgment, followups)
    return record


def _load_cases(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    if not isinstance(cases, list) or not cases:
        raise ValueError("cases JSON은 하나 이상의 cases 배열을 포함해야 합니다")
    for case in cases:
        if not isinstance(case, dict) or not case.get("id") or not case.get("question"):
            raise ValueError("각 case에는 id와 question이 필요합니다")
    return cases


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="서버 base URL, 예: http://49.50.142.35:8000")
    parser.add_argument("--cases", required=True, type=Path, help="테스트 케이스 JSON 파일")
    parser.add_argument("--output-dir", required=True, type=Path, help="회차 결과를 저장할 디렉터리")
    parser.add_argument("--case-id", help="특정 case 하나만 실행")
    parser.add_argument("--timeout", type=float, default=300, help="각 HTTP 요청 제한 시간(초)")
    parser.add_argument("--continue-on-failure", action="store_true", help="실패 후에도 다음 case 실행")
    parser.add_argument("--overwrite", action="store_true", help="기존 회차 결과 덮어쓰기")
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    if args.case_id:
        cases = [case for case in cases if case["id"] == args.case_id]
        if not cases:
            raise SystemExit(f"case를 찾을 수 없습니다: {args.case_id}")
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    status_file = output_dir / "배포상태.json"
    if status_file.exists() and not args.overwrite:
        raise SystemExit(f"이미 결과가 있습니다. 덮어쓰려면 --overwrite를 사용하세요: {output_dir}")

    health_status, health_payload, health_ms = _request_json(f"{args.base_url.rstrip('/')}/health", timeout=args.timeout)
    ready_status, ready_payload, ready_ms = _request_json(f"{args.base_url.rstrip('/')}/ready", timeout=args.timeout)
    deployment = {
        "base_url": args.base_url.rstrip("/"),
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "health": {"status": health_status, "elapsed_ms": round(health_ms, 3), "response": health_payload},
        "ready": {"status": ready_status, "elapsed_ms": round(ready_ms, 3), "response": ready_payload},
    }
    _json_dump(status_file, deployment)
    if health_status != 200 or ready_status != 200:
        raise SystemExit("health/ready 확인에 실패했습니다. 결과는 배포상태.json에 저장했습니다.")

    records = []
    for case in cases:
        case_dir = output_dir / str(case["id"])
        case_dir.mkdir(parents=True, exist_ok=True)
        record = _run_case(args.base_url.rstrip("/"), case, args.timeout, case_dir)
        records.append(record)
        print(f"{record['case_id']}: {'PASS' if record['judgment']['pass'] else 'FAIL'}")
        if not record["judgment"]["pass"] and not args.continue_on_failure:
            break

    with (output_dir / "전체결과.jsonl").open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = "\n".join(
        [
            f"# 테스트 진행 기록",
            "",
            f"- 서버: `{args.base_url.rstrip('/')}`",
            f"- 실행 시각(UTC): `{deployment['captured_at']}`",
            f"- 실행 case 수: `{len(records)}` / `{len(cases)}`",
            f"- 통과: `{sum(1 for record in records if record['judgment']['pass'])}`",
            f"- 실패: `{sum(1 for record in records if not record['judgment']['pass'])}`",
            "",
            "## 다음 작업",
            "",
            "실패 case가 있으면 해당 폴더의 `원인분석.md`에 근본 원인과 회귀 범위를 기록한 뒤 코드 수정, 로컬 테스트, 수정사항별 커밋을 완료하고 다음 case를 실행합니다.",
        ]
    )
    (output_dir / "진행기록.md").write_text(summary + "\n", encoding="utf-8")
    return 0 if records and all(record["judgment"]["pass"] for record in records) else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"TEST RUNNER FAILED: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
