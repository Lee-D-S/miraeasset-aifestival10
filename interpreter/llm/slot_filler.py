"""HyperCLOVA X로 규칙이 못 채운 슬롯만 보충한다.

대회 규칙상 LLM은 HyperCLOVA X만 사용할 수 있다.
안전장치:
- 기업은 universe에 있는 corp_name만 채택한다. 모델이 만든 이름은 버린다.
- enum(intent, doc_group, doc_subtype)에 없는 값은 버린다.
- 호출/파싱 실패 시 규칙 결과를 그대로 쓴다(예외를 밖으로 던지지 않는다).
"""

from __future__ import annotations

import os
import logging
import json
from pathlib import Path
from typing import Any

from ..index.corpus_index import CorpusIndex
from ..models.intent import DOC_GROUPS, DOC_SUBTYPES
from ..pipeline.calculation import SUPPORTED_OPERATIONS

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "slot_fill.txt"
_ALLOWED_INTENTS = {"lookup", "calc", "compare", "list", "change", "exists"}
_LOGGER = logging.getLogger(__name__)


def llm_enabled() -> bool:
    return os.environ.get("INTERPRETER_USE_LLM", "0").strip().lower() in {"1", "true", "yes", "on"}


SLOT_SCHEMA = {
    "type": "object",
    "properties": {
        "corp_names": {"type": "array"},
        "sector": {"type": "string"},
        "intent": {"type": "string"},
        "metric": {"type": "string"},
        "years": {"type": "array"},
        "doc_group": {"type": "string"},
        "doc_subtype": {"type": "string"},
        "operation": {"type": "string"},
        "denominator_metric": {"type": "string"},
        "base_months": {"type": "array", "items": {"type": "integer"}},
        "time_mode": {"type": "string"},
        "basis": {"type": "string"},
        "aggregation_scope": {"type": "string"},
        "correction_mode": {"type": "string"},
        "unresolved_slots": {"type": "array", "items": {"type": "string"}},
    },
}
for _key, _definition in SLOT_SCHEMA["properties"].items():
    if _definition["type"] == "string":
        _definition["type"] = ["string", "null"]
    elif _key == "corp_names":
        _definition["items"] = {"type": "string"}
    elif _key == "years":
        _definition["items"] = {"type": "integer"}


def fill_slots(pre, entities, slots, index: CorpusIndex, *, client: Any | None = None) -> bool:
    """규칙 결과를 제자리에서 보충한다. LLM을 실제로 반영했으면 True."""
    try:
        if client is None:
            return False
        slots.llm_status = "attempted"
        prompt = _build_prompt(pre.text, entities, index)
        prompt += "\n규칙 해석(명시된 값은 보존하고 기본 의도/미해석 조건만 보완): " + json.dumps({
            "intent": slots.intent, "intent_explicit": slots.intent_explicit,
            "metric": slots.metric, "years": slots.years, "basis": slots.basis,
            "doc_group": slots.doc_group, "doc_subtype": slots.doc_subtype,
            "calculation": slots.calculation, "semantic_review": slots.semantic_review,
        }, ensure_ascii=False)
        payload = client.generate_json(
            [{"role": "user", "content": prompt}],
            schema=SLOT_SCHEMA,
            operation="interpreter_slot_fill",
        )
    except Exception as error:
        slots.llm_status = "failed"
        _LOGGER.warning("Interpreter slot fallback failed (%s)", type(error).__name__)
        slots.notes.append(f"LLM 슬롯 보완 실패: {type(error).__name__}")
        return False

    if not isinstance(payload, dict) or not payload:
        slots.llm_status = "empty"
        return False

    # The chat adapter parses JSON but does not enforce the schema.
    payload = {
        key: value for key, value in payload.items()
        if key in SLOT_SCHEMA["properties"] and (
            isinstance(value, list) if key in {"corp_names", "years", "base_months", "unresolved_slots"}
            else isinstance(value, str)
        )
    }

    changed = _merge(payload, entities, slots, index)
    # An arbitrary or wholly invalid object is not a successful review.
    valid_review = any((
        payload.get("intent") in _ALLOWED_INTENTS,
        payload.get("metric") in {m["key"] for m in index.config.metrics.get("metrics", [])},
        changed,
    ))
    slots.llm_status = "validated" if valid_review else "invalid"
    allowed_missing = {"corp_or_sector", "metric", "time", "operation", "basis", "aggregation_scope", "correction_mode", "doc_subtype"}
    slots.unresolved_slots = [v for v in payload.get("unresolved_slots", []) if isinstance(v, str) and v in allowed_missing]
    if changed:
        from ..pipeline.slot_extractor import _resolve_time_mode
        _resolve_time_mode(pre.squashed, slots, index.config)
    return changed


def _build_prompt(question: str, entities, index: CorpusIndex) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    corp_candidates = sorted(index.by_corp_name)
    return template.format(
        question=question,
        corp_names=", ".join(corp_candidates),
        sectors=", ".join(sorted(index.sector_members)),
        doc_groups=", ".join(DOC_GROUPS),
        periodic_subtypes=", ".join(DOC_SUBTYPES["periodic"]),
        exchange_subtypes=", ".join(DOC_SUBTYPES["exchange"]),
        metrics=", ".join(m["key"] for m in index.config.metrics.get("metrics", [])),
        metric_catalog="; ".join(f"{m['key']}={'/'.join(m.get('labels', []))}" for m in index.config.metrics.get("metrics", [])),
        intents=", ".join(sorted(_ALLOWED_INTENTS)),
        operations=", ".join(sorted(SUPPORTED_OPERATIONS)),
        reference_date=index.config.defaults.get("reference_date", ""),
    )


def _merge(payload: dict[str, Any], entities, slots, index: CorpusIndex) -> bool:
    changed = False

    if (not entities.corps and not entities.sector) or entities.ambiguous:
        existing = {corp.corp_name for corp in entities.corps + entities.excluded_corps}
        for name in payload.get("corp_names", []) or []:
            if not isinstance(name, str) or name in existing:
                continue
            if entities.corps and not any(name in item["candidates"] for item in entities.ambiguous):
                continue
            row = index.corp(name)
            if row is None:
                continue
            from ..models.intent import CorpRef

            entities.corps.append(
                CorpRef(
                    corp_name=row["corp_name"],
                    corp_code=row["corp_code"],
                    stock_code=row["stock_code"],
                    listed_name=row["listed_name"],
                    sector=row["sector"],
                    listing_date=row["listing_date"],
                    matched_text=name,
                    match_source="llm",
                )
            )
            existing.add(name)
            changed = True

        selected = {corp.corp_name for corp in entities.corps}
        entities.ambiguous = [
            item for item in entities.ambiguous
            if len(selected.intersection(item["candidates"])) != 1
        ]

    if not entities.sector and not entities.corps:
        sector = payload.get("sector")
        if sector in index.sector_members:
            entities.sector = sector
            excluded = {corp.corp_name for corp in entities.excluded_corps}
            entities.sector_members = [name for name in index.members_of(sector) if name not in excluded]
            changed = True

    if slots.intent in ("unknown", None) or (slots.semantic_review and (not slots.intent_explicit or slots.intent == "lookup")):
        intent = payload.get("intent")
        if intent in _ALLOWED_INTENTS and intent != slots.intent:
            slots.intent = intent
            slots.calculation = {}
            changed = True

    if slots.metric is None:
        metric = payload.get("metric")
        valid = {m["key"] for m in index.config.metrics.get("metrics", [])}
        if metric in valid:
            slots.metric = metric
            slots.metric_confidence = "llm"
            slots.metric_matches = [metric]
            definition = next(m for m in index.config.metrics["metrics"] if m["key"] == metric)
            if slots.doc_group is None:
                slots.doc_group = definition.get("doc_group")
                slots.doc_group_candidates = list(definition.get("doc_group_candidates", []))
                slots.doc_subtype_candidates = list(definition.get("doc_subtype_candidates", []))
                if definition.get("doc_subtype"):
                    slots.doc_subtype_candidates = [definition["doc_subtype"]]
                slots.report_nm_contains = list(definition.get("report_nm_contains", []))
            changed = True

    if not slots.years:
        years = [y for y in (payload.get("years") or []) if type(y) is int and 1900 <= y <= 2099]
        if years:
            slots.years = sorted(dict.fromkeys(years))
            changed = True

    if slots.doc_group is None:
        doc_group = payload.get("doc_group")
        if doc_group in DOC_GROUPS:
            slots.doc_group = doc_group
            changed = True

    if slots.doc_subtype is None:
        subtype = payload.get("doc_subtype")
        if subtype in DOC_SUBTYPES.get(slots.doc_group or "periodic", ()):
            slots.doc_subtype = subtype
            slots.period_explicit = True
            if slots.doc_group in (None, "periodic"):
                slots.base_months = {"annual": [12], "half": [6], "quarter": [3, 9]}.get(subtype, [])
            changed = True

    months = [v for v in payload.get("base_months", []) if type(v) is int and v in {3, 6, 9, 12}]
    if months and (not slots.base_months or slots.base_months == [3, 9]):
        expected = {"annual": {12}, "half": {6}, "quarter": {3, 9}}.get(slots.doc_subtype, set())
        if set(months) <= expected:
            slots.base_months = sorted(set(months))
            changed = True
    for key, allowed in {
        "basis": {"연결", "별도"},
        "aggregation_scope": {"total", "segment", "product", "region", "not_total", "unknown"},
        "correction_mode": {"latest_only", "include_chain", "original_only"},
        "time_mode": {"fiscal", "disclosure"},
    }.items():
        value = payload.get(key)
        if value not in allowed:
            continue
        if key == "basis" and slots.basis_explicit:
            continue
        if key == "correction_mode" and slots.correction_explicit:
            continue
        if key == "time_mode" and slots.time_mode_explicit and slots.time_mode == "disclosure":
            continue
        if getattr(slots, key) != value:
            setattr(slots, key, value)
            changed = True
        if key == "basis":
            slots.basis_explicit = True
        if key == "time_mode":
            slots.time_mode_explicit = True

    operation = payload.get("operation")
    if operation in SUPPORTED_OPERATIONS and not slots.calculation.get("operation"):
        slots.derived_operation = operation
        slots.calculation = {"operation": operation}
        if slots.metric:
            slots.calculation["metric"] = slots.metric
        changed = True

    denominator = payload.get("denominator_metric")
    valid_metrics = {m["key"] for m in index.config.metrics.get("metrics", [])}
    if (slots.calculation.get("operation") in {"ratio_percent", "margin", "divide"}
            and not slots.calculation.get("denominator_metric") and denominator in valid_metrics):
        slots.calculation["denominator_metric"] = denominator
        changed = True

    return changed
