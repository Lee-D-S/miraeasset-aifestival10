"""1단계 CLI.

    python -m interpreter.main "삼성전자의 2025년 연결기준 매출액은?"
    python -m interpreter.main --gold
    python -m interpreter.main --trace "2차전지 기업 중 2025년 설비투자가 가장 큰 곳은?"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from interpreter.index.corpus_index import CorpusIndex
from interpreter.pipeline.build_intent import build_intent


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _run_gold(index: CorpusIndex, verbose: bool) -> int:
    gold_path = Path(__file__).resolve().parent / "tests" / "gold_queries.jsonl"
    cases = [json.loads(line) for line in gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    failed = 0
    for case in cases:
        intent = build_intent(case["question"], index, use_llm=False)
        diffs = _diff(case.get("expect", {}), intent.to_dict())
        status = "PASS" if not diffs else "FAIL"
        if diffs:
            failed += 1
        if diffs or verbose:
            print(f"[{status}] {case['question']}")
            for path, expected, actual in diffs:
                print(f"        {path}: expected={expected!r} actual={actual!r}")
            if verbose:
                print(f"        trace: {intent.trace_summary()}")

    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    return 1 if failed else 0


def _diff(expected, actual, path: str = "") -> list[tuple[str, object, object]]:
    diffs: list[tuple[str, object, object]] = []
    if isinstance(expected, dict):
        for key, value in expected.items():
            diffs.extend(_diff(value, (actual or {}).get(key), f"{path}.{key}" if path else key))
    elif isinstance(expected, list):
        if sorted(map(str, expected)) != sorted(map(str, actual or [])):
            diffs.append((path, expected, actual))
    elif expected != actual:
        diffs.append((path, expected, actual))
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="공시 Agent 1단계 — 질의 이해")
    parser.add_argument("question", nargs="?", help="자연어 질의")
    parser.add_argument("--gold", action="store_true", help="tests/gold_queries.jsonl 실행")
    parser.add_argument("--trace", action="store_true", help="think_trace용 한 줄 요약만 출력")
    parser.add_argument("--filter-only", action="store_true", help="manifest_filter만 출력")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--corpus-dir", type=Path, default=None)
    parser.add_argument("--use-llm", action="store_true", help="규칙으로 못 채운 슬롯만 CLOVA로 보충")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    index = CorpusIndex.load(corpus_dir=args.corpus_dir)

    if args.gold:
        return _run_gold(index, args.verbose)

    if not args.question:
        parser.error("질의를 입력하거나 --gold를 쓰세요.")

    intent = build_intent(args.question, index, use_llm=True if args.use_llm else False)

    if args.trace:
        print(intent.trace_summary())
    elif args.filter_only:
        print(_dump(intent.manifest_filter.to_dict()))
    else:
        print(_dump(intent.to_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
