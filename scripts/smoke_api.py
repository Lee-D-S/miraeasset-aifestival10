"""Run a redacted NCP HTTP smoke test against the public API."""

from __future__ import annotations

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RESPONSE_FIELDS = {"question_id", "question", "retrieved_context", "think_trace", "answer"}


def _get_json(url: str) -> tuple[int, dict]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError(f"request failed: {type(error).__name__}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="NCP API base URL")
    parser.add_argument("--question-id", default="ncp-smoke-1")
    parser.add_argument("--question", default="삼성전자의 2025년 연결기준 매출액은 얼마인가?")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    try:
        health_status, health = _get_json(f"{base}/health")
        if health_status != 200 or health.get("status") != "ok":
            raise RuntimeError("health check failed")
        ready_status, ready = _get_json(f"{base}/ready")
        if ready_status != 200 or ready.get("status") != "ready":
            raise RuntimeError("readiness check failed")
        query = urlencode({"question_id": args.question_id, "question": args.question})
        answer_status, answer = _get_json(f"{base}/answer?{query}")
        if answer_status != 200 or set(answer) != RESPONSE_FIELDS:
            raise RuntimeError("answer response contract failed")
        if not all(isinstance(value, str) for value in answer.values()):
            raise RuntimeError("answer response contains a non-string field")
    except Exception as error:  # noqa: BLE001 - CLI boundary
        print(f"SMOKE FAILED: {type(error).__name__}: {error}")
        return 1
    print(f"SMOKE PASSED: health={health_status} ready={ready_status} answer={answer_status} fields={len(answer)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
