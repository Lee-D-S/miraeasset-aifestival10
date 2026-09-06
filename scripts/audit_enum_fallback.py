"""Read-only runtime audit of Interpreter enums and fallback boundaries.

Run from the repository root with python -m scripts.audit_enum_fallback.
Add --live to make actual HyperCLOVA calls for the question cases.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from integration.clova import ClovaChatClient
from integration.supervisor import DeterministicSupervisor
from interpreter.index.corpus_index import CorpusIndex
from interpreter.llm.slot_filler import fill_slots
from interpreter.pipeline.build_intent import build_intent
from interpreter.pipeline.entity_linker import EntityResult
from interpreter.pipeline.preprocess import preprocess
from interpreter.pipeline.slot_extractor import SlotResult
from interpreter.pipeline.calculation import SUPPORTED_OPERATIONS
from reasoner.contracts import ReasonerDocument, ReasonerFact, adapt_interpreter_intent
from reasoner.agents.fact_extraction import extract_facts
from reasoner.deterministic.calculation_planner import (
    build_state_analysis_plan, validate_analysis_plan, SUPPORTED_OPERATIONS as PLAN_OPERATIONS,
)
from reasoner.grounding import requested_aggregation_scope, fact_matches_intent


class ProbeClient:
    def __init__(self, delegate=None, payload=None):
        self.delegate = delegate
        self.payload = payload or {}
        self.calls = []
        self.responses = []

    def generate_json(self, messages, *, schema, **kwargs):
        self.calls.append(kwargs.get("operation"))
        if self.delegate:
            response = self.delegate.generate_json(messages, schema=schema, **kwargs)
            self.responses.append(response)
            return response
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


CASES = [
    ("complete", "삼성전자 2025년 매출액은?"),
    ("missing_metric", "삼성전자 2025년 외형 규모는?"),
    ("unknown_metric", "삼성전자 2025년 고객 이탈률은?"),
    ("missing_year", "삼성전자 이천이십오년 매출액은?"),
    ("no_time", "삼성전자 매출액은?"),
    ("no_company", "2025년 매출액은?"),
    ("ambiguous_company", "삼성 2025년 매출액은?"),
    ("add", "삼성전자 2024년과 2025년 매출액을 더한 값은?"),
    ("average", "삼성전자 2024년과 2025년 매출액의 평균은?"),
    ("missing_operation", "삼성전자 2024년 대비 2025년 매출액의 차액은?"),
    ("annual_default", "삼성전자 2025년 매출액은?"),
    ("half_paraphrase", "삼성전자 2025년 첫 여섯 달 매출액은?"),
    ("llm_half", "삼성전자 이천이십오년 상반기 매출액은?"),
    ("disclosure_time", "삼성전자가 2025년에 제출한 사업보고서 매출액은?"),
    ("original_only", "삼성전자 2025년 공급계약 정정본을 제외하고 원공시만 알려줘"),
    ("latest_only", "삼성전자 2025년 공급계약 정정 후 최종 내용만 알려줘"),
    ("scope_paraphrase", "삼성전자 2025년 국내와 해외로 나눈 매출액은?"),
    ("scope_explicit", "삼성전자 2025년 지역별 매출액은?"),
    ("basis_paraphrase", "삼성전자 2025년 자회사를 빼고 본사만의 매출액은?"),
    ("basis_explicit", "삼성전자 2025년 별도기준 매출액은?"),
    ("unsafe", "삼성전자 지금 사도 되나?"),
    ("outside_corpus", "삼성전자 2030년 매출액은?"),
]


def audit(index, live):
    supervisor = DeterministicSupervisor()
    delegate = ClovaChatClient(timeout=25, max_retries=0) if live else None
    rows = []
    for name, question in CASES:
        client = ProbeClient(delegate)
        result = build_intent(question, index, True, client)
        value = result.to_dict()
        row = {
            "id": name, "question": question, "calls": client.calls,
            "responses": client.responses,
            **{key: value[key] for key in ("intent", "question_type", "route", "metric", "calculation",
                "time", "basis", "correction_mode", "llm_used", "missing_slots", "assumptions")},
            "scope": requested_aggregation_scope(adapt_interpreter_intent(value)),
            "filter": value["manifest_filter"],
            "next_action": supervisor.decide(phase="after_interpreter", state={"question": question,
                "intent": value, "route": result.route}).action,
        }
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("id", "calls", "intent", "route", "metric", "calculation",
            "basis", "correction_mode", "scope", "next_action")}, ensure_ascii=False), flush=True)

    accepted_metrics = []
    for entry in index.config.metrics["metrics"]:
        slots = SlotResult()
        fill_slots(preprocess("미해결 질문"), EntityResult(), slots, index,
                   client=ProbeClient(payload={"metric": entry["key"]}))
        if slots.metric == entry["key"]:
            accepted_metrics.append(slots.metric)
    accepted_operations = []
    for operation in sorted(SUPPORTED_OPERATIONS):
        slots = SlotResult(intent="calc", metric="revenue")
        fill_slots(preprocess("미해결 질문"), EntityResult(), slots, index,
                   client=ProbeClient(payload={"operation": operation}))
        if slots.calculation.get("operation") == operation:
            accepted_operations.append(operation)

    enum_probes = {}
    for key, values in {
        "intent": ["lookup", "calc", "compare", "list", "change", "exists", "unknown", "invalid"],
        "doc_group": ["periodic", "major", "exchange", "holding", "invalid"],
        "doc_subtype": ["annual", "half", "quarter", "invalid"],
    }.items():
        enum_probes[key] = []
        for candidate in values:
            slots = SlotResult()
            changed = fill_slots(preprocess("미해결 질문"), EntityResult(), slots, index,
                                client=ProbeClient(payload={key: candidate}))
            enum_probes[key].append({"input": candidate, "output": getattr(slots, key), "changed": changed})

    plan_operations = {}
    for operation in [*sorted(PLAN_OPERATIONS), "median"]:
        proposal = {"requirements": [{"id": "revenue", "metric": "revenue"}],
                    "steps": [{"id": "result", "operation": operation, "inputs": ["revenue"]}],
                    "output": {"ref": "result"}}
        try:
            validate_analysis_plan(proposal)
            plan_operations[operation] = "accepted"
        except ValueError:
            plan_operations[operation] = "rejected"

    failures = []
    for name, payload in [
        ("invalid_enum", {"metric": "customer_churn", "intent": "magic", "operation": "median",
                          "doc_group": "news", "doc_subtype": "monthly"}),
        ("provider_error", RuntimeError("audit simulated provider error")),
        ("ignored_fields", {"basis": "별도", "correction_mode": "original_only", "time_mode": "disclosure",
                            "aggregation_scope": "region", "question_type": "event", "route": "unsafe"}),
    ]:
        result = build_intent("삼성전자 2025년 미등록항목", index, True, ProbeClient(payload=payload))
        failures.append({"id": name, "result": result.to_dict()})

    # Simulate the exact merge when only the LLM understands a half-year.
    period_result = build_intent("삼성전자 이천이십육년 첫 여섯 달 외형", index, True,
        ProbeClient(payload={"metric": "revenue", "years": [2026], "doc_subtype": "half"}))

    state = {"question": "삼성전자 2025년 매출액 중앙값", "intent": {
        "route": "ok", "intent": "calc", "question_type": "calculation", "metric": "revenue",
        "time": {"years": [2025]}, "corps": [{"corp_name": "삼성전자"}]}}
    planner = []
    for enabled in (False, True):
        client = ProbeClient(payload={"status": "ready", "requirements": []})
        output = build_state_analysis_plan(state, llm_enabled=enabled, llm_client=client)
        planner.append({"enabled": enabled, "calls": client.calls, **output})

    route_probes = {route: supervisor.decide(phase="after_interpreter", state={"route": route}).action
                    for route in ("ok", "need_clarify", "unanswerable", "unsafe", "bogus", "")}
    back_probes = {
        "search_empty_first": supervisor.decide(phase="after_retriever", state={}).action,
        "search_empty_after_retry": supervisor.decide(phase="after_retriever", state={"retry_num": 1}).action,
        "insufficient_evidence": supervisor.decide(phase="after_reasoner", state={
            "reasoner_result": {"status": "insufficient_evidence"}}).action,
    }
    fact_intent = adapt_interpreter_intent({"route": "ok"})
    fact = ReasonerFact.from_dict({"metric": "revenue", "kind": "invalid"})
    date_facts = extract_facts([ReasonerDocument(id="audit", source="audit", text="계약일 2025년 3월 1일")], fact_intent)
    return {"mode": "live" if live else "empty_response_probe", "questions": rows,
        "accepted_metrics": accepted_metrics, "accepted_operations": accepted_operations,
        "enum_probes": enum_probes, "plan_operation_validation": plan_operations,
        "failure_probes": failures, "llm_half_period": period_result.to_dict(), "planner_probes": planner,
        "route_probes": route_probes, "back_probes": back_probes,
        "fact_probes": {"invalid_kind_accepted": fact_matches_intent(fact, fact_intent),
                        "extracted_kinds": sorted({f.kind for f in date_facts})},
        "settings": {key: os.getenv(key, "<unset>") for key in (
            "INTERPRETER_USE_LLM", "QUERY_PLANNER_LLM_ENABLED", "CLOVA_SEMANTIC_MAX_TOKENS")}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--gold", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load_dotenv()
    index = CorpusIndex.load()
    result = audit(index, args.live)
    if args.gold:
        from interpreter.tests.run_checks import GOLD_PATH, _subset_diffs
        client = ProbeClient(ClovaChatClient(timeout=25, max_retries=1)) if args.live else None
        results = []
        for line in GOLD_PATH.read_text(encoding="utf-8").splitlines():
            case = json.loads(line)
            intent = build_intent(case["question"], index, args.live, client)
            differences = _subset_diffs(case.get("expect", {}), intent.to_dict())
            results.append({"id": case["id"], "differences": differences, "llm_status": intent.llm_status})
            print(json.dumps(results[-1], ensure_ascii=False), flush=True)
        result["gold"] = results
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
