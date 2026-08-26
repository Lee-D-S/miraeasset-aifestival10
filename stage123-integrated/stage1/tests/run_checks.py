"""pytest 없이 돌리는 1단계 검증 러너.

    python -m stage1.tests.run_checks

pytest가 설치돼 있으면 test_build_intent.py가 동일한 항목을 커버한다.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from stage1.index.corpus_index import CorpusIndex
from stage1.pipeline.build_intent import build_intent

GOLD_PATH = Path(__file__).resolve().parent / "gold_queries.jsonl"

_failures: list[str] = []


def check(condition: bool, label: str) -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}")
        _failures.append(label)


def _subset_diffs(expected, actual, path: str = "") -> list[str]:
    diffs: list[str] = []
    if isinstance(expected, dict):
        for key, value in expected.items():
            diffs.extend(_subset_diffs(value, (actual or {}).get(key), f"{path}.{key}" if path else key))
    elif isinstance(expected, list):
        if sorted(map(str, expected)) != sorted(map(str, actual or [])):
            diffs.append(f"{path}: expected={expected} actual={actual}")
    elif expected != actual:
        diffs.append(f"{path}: expected={expected!r} actual={actual!r}")
    return diffs


def run_gold(index: CorpusIndex) -> None:
    print("[gold_queries.jsonl]")
    cases = [json.loads(line) for line in GOLD_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    failed = 0
    for case in cases:
        intent = build_intent(case["question"], index, use_llm=False)
        diffs = _subset_diffs(case.get("expect", {}), intent.to_dict())
        if diffs:
            failed += 1
            print(f"  FAIL  {case['id']} {case['question']}")
            for diff in diffs:
                print(f"          {diff}")
    print(f"  {len(cases) - failed}/{len(cases)} passed")
    if failed:
        _failures.append(f"gold {failed} case(s)")


def run_invariants(index: CorpusIndex) -> None:
    print("\n[별칭 → corp_name 정규화]")
    pairs = {
        "현대차": "현대자동차",
        "KT": "케이티",
        "엔씨소프트": "NC",
        "삼성화재": "삼성화재해상보험",
        "LS ELECTRIC": "엘에스일렉트릭",
        "LIG넥스원": "LIG디펜스앤에어로스페이스",
        "네이버": "NAVER",
        "포스코": "POSCO홀딩스",
        "JYP": "JYP Ent",
    }
    for spoken, canonical in pairs.items():
        intent = build_intent(f"{spoken}의 2025년 매출액은?", index, use_llm=False)
        check(intent.manifest_filter.corp_names == [canonical], f"{spoken} → {canonical}")

    print("\n[route != ok 이면 필터를 비운다]")
    for question in ["삼성전자의 2022년 매출액은?", "삼성전자 지금 사도 되나?", "매출액 얼마야?"]:
        intent = build_intent(question, index, use_llm=False)
        cleared = (
            intent.route != "ok"
            and intent.manifest_filter.corp_names == []
            and intent.manifest_filter.doc_group is None
            and intent.doc_count is None
        )
        check(cleared, f"{question} → route={intent.route}, filter 비움")

    print("\n[건수 0 ≠ 결측]")
    intent = build_intent("시프트업의 2023년 사업보고서 매출액은?", index, use_llm=False)
    check(intent.route == "ok" and intent.doc_count == 0 and bool(intent.warnings),
          "상장 전 연도 → ok + 0건 + 경고")

    print("\n[보고기간과 접수일을 섞지 않는다]")
    intent = build_intent("삼성전자의 2025년 사업보고서 매출액은?", index, use_llm=False)
    check(
        intent.manifest_filter.base_years == [2025]
        and intent.manifest_filter.rcept_from is None
        and (intent.doc_count or 0) > 0,
        "FY2025 사업보고서(2026-03 접수)가 탈락하지 않는다",
    )

    print("\n[섹터 질의에서 기업을 임의로 고르지 않는다]")
    intent = build_intent("2차전지 기업 중 2025년 설비투자가 가장 큰 곳은?", index, use_llm=False)
    check(
        intent.manifest_filter.corp_names == []
        and intent.manifest_filter.sector == "2차전지"
        and len(intent.sector_members) == 3,
        "sector만 넘기고 멤버 3사를 힌트로 준다",
    )

    print("\n[major는 report_nm으로 구분한다]")
    intent = build_intent("셀트리온이 2025년에 발행한 전환사채 알려줘", index, use_llm=False)
    check(
        intent.manifest_filter.doc_group == "major"
        and intent.manifest_filter.doc_subtype is None
        and intent.manifest_filter.report_nm_contains == ["전환사채권발행결정"],
        "CB → major + report_nm_contains",
    )

    print("\n[정정 이력 질의는 원본·정정본을 함께 본다]")
    intent = build_intent("삼성전자 2025년 사업보고서 정정공시가 있었나?", index, use_llm=False)
    check(intent.manifest_filter.is_correction is None, "correction_mode=include_chain")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    index = CorpusIndex.load()
    run_gold(index)
    run_invariants(index)

    print()
    if _failures:
        print(f"FAILED ({len(_failures)}): " + "; ".join(_failures))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
