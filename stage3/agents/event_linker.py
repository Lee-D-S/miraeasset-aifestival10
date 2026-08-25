from __future__ import annotations

import re
from typing import Any, Iterable

from stage3.contracts import Stage3Document, Stage3Intent


def _company(document: Stage3Document) -> str:
    return str(document.metadata.get("corp_name", ""))


def _event_kind(document: Stage3Document) -> str | None:
    text = f"{document.metadata.get('report_nm', '')} {document.text}"
    if document.metadata.get("is_correction") or "정정" in text:
        return "correction"
    if "해지" in text or "철회" in text:
        return "termination"
    if "체결" in text or "발행결정" in text or "결정" in text:
        return "origin"
    return None


def _event_key(document: Stage3Document) -> str:
    text = re.sub(r"\s+", "", f"{document.metadata.get('report_nm', '')} {document.text}")
    for marker in ("계약명", "계약상대방", "계약내용"):
        index = text.find(marker)
        if index >= 0:
            return text[index:index + 80]
    return text[:80]


def link_events(documents: Iterable[Stage3Document], intent: Stage3Intent) -> list[dict[str, Any]]:
    """Return conservative event links with source IDs; do not invent relationships."""

    grouped: dict[tuple[str, str], list[Stage3Document]] = {}
    for document in documents:
        kind = _event_kind(document)
        if kind:
            grouped.setdefault((_company(document), _event_key(document)), []).append(document)

    links: list[dict[str, Any]] = []
    for (company, key), items in grouped.items():
        origins = [item for item in items if _event_kind(item) == "origin"]
        followups = [item for item in items if _event_kind(item) in {"termination", "correction"}]
        for followup in followups:
            if not origins:
                continue
            origin = sorted(origins, key=lambda item: str(item.metadata.get("rcept_dt", "")))[0]
            relation = "termination" if _event_kind(followup) == "termination" else "correction"
            links.append({
                "relation": relation,
                "company": company or None,
                "key": key,
                "origin_document_id": origin.id,
                "followup_document_id": followup.id,
                "source_ids": [origin.id, followup.id],
                "confidence": 0.75 if key else 0.4,
                "evidence": [origin.text[:300], followup.text[:300]],
            })
    return links
