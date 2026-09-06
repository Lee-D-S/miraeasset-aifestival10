"""Hit /answer for the Reasoner E2E questions that failed the last local round."""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"
QUESTIONS = [
    ("T02", "삼성전자의 최근 3년 매출액을 알려줘", "2023·2024·2025 각각"),
    ("T03", "삼성전자의 2025년 부채비율은?", "부채비율 %"),
    ("T04", "삼성전자의 2025년 자기자본비율은?", "자기자본비율 %"),
    ("T05", "삼성전자의 2024년과 2025년 매출액을 비교해줘", "두 해 + 증감"),
    ("T06", "삼성전자의 최근 3년 매출액 추이를 알려줘", "2023~2025 추이"),
    ("T07", "현대차의 지난 분기 영업이익은?", "정상 규모 (e+25면 실패)"),
]


def call(qid: str, question: str) -> dict:
    query = urllib.parse.urlencode({"question_id": qid, "question": question})
    req = urllib.request.Request(
        f"{BASE}/answer?{query}", headers={"Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    wanted = set(sys.argv[1:]) if len(sys.argv) > 1 else {row[0] for row in QUESTIONS}
    for qid, question, expect in QUESTIONS:
        if qid not in wanted:
            continue
        print("\n" + "=" * 72)
        print(f"{qid}  expect: {expect}")
        print(f"Q: {question}")
        try:
            payload = call(qid, question)
        except Exception as error:  # noqa: BLE001 - CLI boundary
            print(f"ERROR: {error}")
            continue
        print(payload.get("answer", "")[:1500])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
