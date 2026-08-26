from __future__ import annotations

import argparse
import json

from dotenv import load_dotenv

from .composition import Stage123Application


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage1 → Stage2 → Stage3 local E2E runner")
    parser.add_argument("question", help="자연어 공시 질의")
    parser.add_argument("--question-id", default=None)
    parser.add_argument("--db-backend", choices=("json", "production"), default=None)
    parser.add_argument("--execution-mode", choices=("stdlib", "langgraph"), default=None)
    parser.add_argument("--json-path", default=None)
    parser.add_argument("--include-internal", action="store_true", help="디버깅용 내부 계약을 JSON에 포함")
    return parser


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)
    import os

    if args.db_backend:
        os.environ["E2E_DB_BACKEND"] = args.db_backend
    if args.execution_mode:
        os.environ["STAGE3_EXECUTION_MODE"] = args.execution_mode
    if args.json_path:
        os.environ["LOCAL_JSON_DB_PATH"] = args.json_path

    run = Stage123Application.from_environment().run(
        question_id=args.question_id,
        question=args.question,
    )
    payload: dict[str, object] = dict(run.response)
    if args.include_internal:
        payload["status"] = run.status
        payload["stage1_intent"] = run.intent
        payload["stage2_result"] = run.stage2_result
        payload["stage3_result"] = run.stage3_result.to_dict()
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if run.status in {"success", "ok", "need_clarify", "unanswerable", "unsafe", "insufficient_evidence"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
