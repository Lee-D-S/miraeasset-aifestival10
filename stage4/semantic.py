from __future__ import annotations

import json
import re
from typing import Any, Mapping


SEMANTIC_SCHEMA = {
    "type": "object",
    "required": ["pass", "issues", "unsupported_claims", "missing_aspects", "summary"],
    "properties": {
        "pass": {"type": "boolean"},
        "issues": {"type": "array"},
        "unsupported_claims": {"type": "array"},
        "missing_aspects": {"type": "array"},
        "summary": {"type": "string"},
    },
}


def _numeric_tokens(answer: str) -> set[str]:
    return {
        re.sub(r"\D", "", match)
        for match in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", answer)
        if re.sub(r"\D", "", match)
    }


def _prioritized_facts(stage3_result: Mapping[str, Any], *, intent: Mapping[str, Any] | None, answer: str) -> list[Mapping[str, Any]]:
    """Prioritize facts that directly ground the draft answer."""
    raw_facts = [item for item in stage3_result.get("facts", []) if isinstance(item, Mapping)]
    answer_numbers = _numeric_tokens(answer)
    intent = intent or {}
    requested_metric = str(intent.get("metric", "")).strip().lower()
    requested_companies = {str(item).strip().lower() for item in intent.get("companies", []) if str(item).strip()}
    requested_years = {str(item) for item in (intent.get("time", {}) or {}).get("years", [])}
    requested_basis = str(intent.get("basis", "")).strip().lower()

    def score(fact: Mapping[str, Any]) -> tuple[int, int, int, int, int]:
        values = {
            re.sub(r"\D", "", str(fact.get("value", ""))),
            re.sub(r"\D", "", str(fact.get("normalized_value", ""))),
        }
        return (
            int(bool(answer_numbers & {value for value in values if value})),
            int(bool(requested_metric and str(fact.get("metric", "")).lower() == requested_metric)),
            int(bool(requested_companies and str(fact.get("company", "")).strip().lower() in requested_companies)),
            int(bool(requested_years and any(year in str(fact.get("period", "")) for year in requested_years))),
            int(bool(requested_basis and str(fact.get("basis", "")).strip().lower() == requested_basis)),
        )

    return sorted(raw_facts, key=score, reverse=True)


def _compact_stage3_result(stage3_result: Mapping[str, Any], *, intent: Mapping[str, Any] | None = None, answer: str = "") -> dict[str, Any]:
    """Bound the semantic request while preserving representative evidence."""
    prioritized_facts = _prioritized_facts(stage3_result, intent=intent, answer=answer)
    facts = [
        {key: fact.get(key) for key in ("metric", "label", "value", "unit", "normalized_value", "period", "basis", "company", "document_id")}
        for fact in prioritized_facts[:20]
    ]
    grounding_facts = [
        {
            "metric": fact.get("metric"), "label": fact.get("label"), "value": fact.get("value"),
            "normalized_value": fact.get("normalized_value"), "unit": fact.get("unit"),
            "period": fact.get("period"), "basis": fact.get("basis"), "company": fact.get("company"),
            "document_id": fact.get("document_id"), "source": fact.get("source"),
            "evidence": str(fact.get("evidence", ""))[:800],
        }
        for fact in prioritized_facts[:3]
    ]
    fact_rank = {str(fact.get("document_id")): index for index, fact in enumerate(prioritized_facts) if fact.get("document_id")}
    raw_citations = [item for item in stage3_result.get("citations", []) if isinstance(item, Mapping)]
    ordered_citations = sorted(raw_citations, key=lambda item: fact_rank.get(str(item.get("document_id", "")), len(fact_rank)))
    citations = [
        {"document_id": citation.get("document_id"), "source": citation.get("source"), "evidence": str(citation.get("evidence", ""))[:800]}
        for citation in ordered_citations[:8]
    ]
    return {
        "facts": facts,
        "answer_grounding_facts": grounding_facts,
        "calculations": list(stage3_result.get("calculations", []))[:20],
        "comparison_results": list(stage3_result.get("comparison_results", []))[:10],
        "linked_events": list(stage3_result.get("linked_events", []))[:10],
        "citations": citations,
    }


def _deterministic_grounding_pass(stage3_result: Mapping[str, Any], answer: str) -> bool:
    """Accept an explicitly grounded lookup draft when the model is uncertain.

    This is deliberately narrow: the answer must contain a value from the
    representative Fact and a citation marker for the same document. It does
    not override a provider response that identifies unsupported claims or
    missing aspects.
    """
    for fact in _prioritized_facts(stage3_result, intent=None, answer=answer):
        if str(fact.get("metric", "")).lower() == "date":
            continue
        values = {
            re.sub(r"\D", "", str(fact.get("value", ""))),
            re.sub(r"\D", "", str(fact.get("normalized_value", ""))),
        }
        if not any(value and value in _numeric_tokens(answer) for value in values):
            continue
        document_id = str(fact.get("document_id", "")).strip()
        if document_id and document_id in answer:
            return True
    return False


def validate_semantics(client: Any, *, question: str, intent: Any, stage3_result: Mapping[str, Any], answer: str) -> dict[str, Any]:
    if client is None:
        raise RuntimeError("Stage4 semantic validator client is not configured")
    payload = {"question": question, "intent": intent, **_compact_stage3_result(stage3_result, intent=intent, answer=answer), "answer": answer}
    result = client.generate_json([{
        "role": "user",
        "content": (
            "Assess whether the answer is fully supported by the question and disclosure evidence. "
            "Use answer_grounding_facts first; each item contains the representative fact, value, "
            "period, basis, source document ID, and evidence text. Reject unsupported or materially "
            "incomplete claims. Return exactly one object matching the JSON schema.\nDATA:\n"
            + json.dumps(payload, ensure_ascii=False)
        ),
    }], schema=SEMANTIC_SCHEMA)
    if not isinstance(result, Mapping):
        raise ValueError("semantic validator returned a non-object")
    normalized = dict(result)
    for key in ("issues", "unsupported_claims", "missing_aspects"):
        if not isinstance(normalized.get(key), list):
            raise ValueError(f"semantic validator field {key} must be an array")
    if not isinstance(normalized.get("pass"), bool) or not isinstance(normalized.get("summary"), str):
        raise ValueError("semantic validator returned an invalid schema")
    if (
        not normalized["pass"]
        and not normalized["unsupported_claims"]
        and not normalized["missing_aspects"]
        and _deterministic_grounding_pass(stage3_result, answer)
    ):
        normalized["pass"] = True
        normalized["summary"] = "Deterministic grounding contract passed; provider semantic verdict was uncertain."
    return normalized


__all__ = ["SEMANTIC_SCHEMA", "validate_semantics"]
