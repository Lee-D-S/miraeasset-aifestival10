"""HyperCLOVA X로 규칙이 못 채운 슬롯만 보충한다.

대회 규칙상 LLM은 HyperCLOVA X만 사용할 수 있다.
안전장치:
- 기업은 universe에 있는 corp_name만 채택한다. 모델이 만든 이름은 버린다.
- enum(intent, doc_group, doc_subtype)에 없는 값은 버린다.
- 호출/파싱 실패 시 규칙 결과를 그대로 쓴다(예외를 밖으로 던지지 않는다).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from ..index.corpus_index import CorpusIndex
from ..models.intent import DOC_GROUPS, DOC_SUBTYPES

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "slot_fill.txt"
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

_ALLOWED_INTENTS = {"lookup", "calc", "compare", "list", "change", "exists"}


def llm_enabled() -> bool:
    return os.environ.get("STAGE1_USE_LLM", "0") == "1"


def fill_slots(pre, entities, slots, index: CorpusIndex) -> bool:
    """규칙 결과를 제자리에서 보충한다. LLM을 실제로 반영했으면 True."""
    if not llm_enabled():
        return False

    try:
        payload = _call_clova(_build_prompt(pre.text, entities, index))
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


def _call_clova(prompt: str) -> Optional[dict[str, Any]]:
    api_key = os.environ.get("CLOVA_API_KEY")
    endpoint = os.environ.get("CLOVA_ENDPOINT")
    if not api_key or not endpoint:
        return None

    import requests

    response = requests.post(
        endpoint,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "messages": [
                {"role": "system", "content": "너는 공시 질의에서 슬롯만 추출하는 파서다. JSON만 출력한다."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.0,
            "topP": 0.8,
            "maxTokens": 512,
        },
        timeout=float(os.environ.get("CLOVA_TIMEOUT", "8")),
    )
    response.raise_for_status()
    body = response.json()
    text = (
        body.get("result", {})
        .get("message", {})
        .get("content")
        or body.get("choices", [{}])[0].get("message", {}).get("content", "")
    )
    match = _JSON_BLOCK.search(text or "")
    return json.loads(match.group(0)) if match else None


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

    return changed
