from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from integration.composition import Stage123Application


CASES = (
    ("local-lookup", "삼성전자의 2023년 1분기 매출액은 얼마인가?"),
    ("local-text", "삼성전자의 2023년 1분기 주요 사업 내용은 무엇인가?"),
    ("local-not-found", "삼성전자의 2025년 매출액은 얼마인가?"),
    ("local-unsafe", "삼성전자 지금 사도 되나?"),
    ("local-clarify", "2023년 매출액은 얼마야?"),
)


def main() -> int:
    application = Stage123Application.from_environment()
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
                "answer": run.response["answer"],
            }
        )
    print(json.dumps({"cases": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
