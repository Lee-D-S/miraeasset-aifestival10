"""HyperCLOVA X로 규칙이 못 채운 슬롯만 보충한다.

대회 규칙상 LLM은 HyperCLOVA X만 사용할 수 있다.
안전장치:
- 기업은 universe에 있는 corp_name만 채택한다. 모델이 만든 이름은 버린다.
- enum(intent, doc_group, doc_subtype)에 없는 값은 버린다.
- 호출/파싱 실패 시 규칙 결과를 그대로 쓴다(예외를 밖으로 던지지 않는다).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..index.corpus_index import CorpusIndex
from ..models.intent import DOC_GROUPS, DOC_SUBTYPES
from ..pipeline.calculation import SUPPORTED_OPERATIONS

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "slot_fill.txt"
_ALLOWED_INTENTS = {"lookup", "calc", "compare", "list", "change", "exists"}


def llm_enabled() -> bool:
    return os.environ.get("INTERPRETER_USE_LLM", "0") == "1"


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
    },
}


def fill_slots(pre, entities, slots, index: CorpusIndex, *, client: Any | None = None) -> bool:
    """규칙 결과를 제자리에서 보충한다. LLM을 실제로 반영했으면 True."""
    try:
        if client is None:
            return False
        payload = client.generate_json(
            [{"role": "user", "content": _build_prompt(pre.text, entities, index)}],
            schema=SLOT_SCHEMA,
        )
    except Exception:
        return False

    if not payload:
        return False

    return _merge(payload, entities, slots, index)


def _build_prompt(question: str, entities, index: CorpusIndex) -> str:
    template = _PROMPT_PATH.read_text(encoding="utf-8")
    corp_candidates = [c.corp_name for c in entities.corps] or sorted(index.by_corp_name)
    return template.format(
        question=question,
        corp_names=", ".join(corp_candidates),
        sectors=", ".join(sorted(index.sector_members)),
        doc_groups=", ".join(DOC_GROUPS),
        periodic_subtypes=", ".join(DOC_SUBTYPES["periodic"]),
        exchange_subtypes=", ".join(DOC_SUBTYPES["exchange"]),
        metrics=", ".join(m["key"] for m in index.config.metrics.get("metrics", [])),
        intents=", ".join(sorted(_ALLOWED_INTENTS)),
    )


def _merge(payload: dict[str, Any], entities, slots, index: CorpusIndex) -> bool:
    changed = False

    if not entities.corps:
        for name in payload.get("corp_names", []) or []:
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
            changed = True

    if not entities.sector:
        sector = payload.get("sector")
        if sector in index.sector_members:
            entities.sector = sector
            entities.sector_members = index.members_of(sector)
            changed = True

    if slots.intent in ("unknown", None):
        intent = payload.get("intent")
        if intent in _ALLOWED_INTENTS:
            slots.intent = intent
            changed = True

    if slots.metric is None:
        metric = payload.get("metric")
        valid = {m["key"] for m in index.config.metrics.get("metrics", [])}
        if metric in valid:
            slots.metric = metric
            slots.metric_confidence = "llm"
            changed = True

    if not slots.years:
        years = [y for y in (payload.get("years") or []) if isinstance(y, int)]
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
            slots.period_explicit = False
            changed = True

    operation = payload.get("operation")
    if operation in SUPPORTED_OPERATIONS and not slots.calculation.get("operation"):
        slots.calculation = {"operation": operation}
        if slots.metric:
            slots.calculation["metric"] = slots.metric
        denominator = payload.get("denominator_metric")
        if isinstance(denominator, str) and denominator:
            slots.calculation["denominator_metric"] = denominator
        changed = True

    return changed
