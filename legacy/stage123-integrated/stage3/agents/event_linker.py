from __future__ import annotations

import re
from typing import Any, Iterable

from stage3.contracts import AgentResult, Stage3Document, Stage3Intent
from stage3.state import Stage3GraphState


_CORRECTION_MARKERS = ("[기재정정]", "기재정정", "정정공시", "정정")
_ORIGIN_ID_KEYS = (
    "original_rcept_no",
    "origin_rcept_no",
    "original_contract_rcept_no",
    "parent_rcept_no",
    "original_doc_id",
    "origin_document_id",
    "original_contract_doc_id",
)
_FIELD_LABELS = {
    "contract_name": ("체결계약명", "계약명", "원계약"),
    "counterparty": ("계약상대방", "계약 상대방"),
    "amount": ("계약금액(원)", "계약금액", "기존 계약금액", "해지금액"),
    "date": ("계약일자", "계약 체결일", "계약일"),
}


def _company(document: Stage3Document) -> str:
    return str(document.metadata.get("corp_name", ""))


def _all_metadata(document: Stage3Document) -> dict[str, Any]:
    return {**document.raw, **document.metadata}


def _event_kind(document: Stage3Document) -> str | None:
    metadata = _all_metadata(document)
    text = f"{metadata.get('report_nm', '')} {document.text}"
    if metadata.get("is_correction") or any(marker in text for marker in _CORRECTION_MARKERS):
        return "correction"
    if "해지" in text or "철회" in text:
        return "termination"
    if "체결" in text or "발행결정" in text or "결정" in text:
        return "origin"
    return None


def _normalized_report_name(document: Stage3Document) -> str:
    report_name = str(_all_metadata(document).get("report_nm", ""))
    for marker in _CORRECTION_MARKERS:
        report_name = report_name.replace(marker, "")
    return re.sub(r"[^가-힣A-Za-z0-9]", "", report_name).lower()


def _correction_key(document: Stage3Document) -> tuple[str, str, str, str, str, str]:
    metadata = _all_metadata(document)
    return (
        _company(document),
        str(metadata.get("doc_group", "")),
        str(metadata.get("doc_subtype", "")),
        str(metadata.get("base_year", "")),
        str(metadata.get("base_month", "")),
        _normalized_report_name(document),
    )


def _is_correction(document: Stage3Document) -> bool:
    return _event_kind(document) == "correction"


def _correction_documents(documents: list[Stage3Document], intent: Stage3Intent) -> list[Stage3Document]:
    mode = intent.correction_mode or "latest_only"
    if mode == "original_only":
        return [document for document in documents if not _is_correction(document)]
    if mode != "latest_only":
        return documents
    grouped: dict[tuple[str, str, str, str, str, str], list[Stage3Document]] = {}
    for document in documents:
        if _is_correction(document):
            grouped.setdefault(_correction_key(document), []).append(document)
    latest_ids: set[str] = set()
    for items in grouped.values():
        latest = max(items, key=lambda item: (str(_all_metadata(item).get("rcept_dt", "")), str(_all_metadata(item).get("rcept_no", ""))))
        latest_ids.add(latest.id)
    return [document for document in documents if not _is_correction(document) or document.id in latest_ids]


def _explicit_origin_id(document: Stage3Document) -> str | None:
    metadata = _all_metadata(document)
    for key in _ORIGIN_ID_KEYS:
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    text = document.text or ""
    match = re.search(r"(?:원공시|원계약|원문서|최초)\s*(?:접수번호|공시번호|문서번호)?\s*[:：]?\s*(\d{10,})", text)
    return match.group(1) if match else None


def _field_value(text: str, labels: tuple[str, ...]) -> str | None:
    label_pattern = "|".join(re.escape(label) for label in sorted(labels, key=len, reverse=True))
    match = re.search(rf"(?:{label_pattern})\s*[:：]?\s*([^|\n]+)", text)
    if not match:
        return None
    value = re.sub(r"\s+", " ", match.group(1)).strip(" :：")
    return value or None


def _contract_identity(document: Stage3Document) -> dict[str, str | None]:
    text = re.sub(r"\s+", " ", document.text or "")
    values: dict[str, str | None] = {}
    for field, labels in _FIELD_LABELS.items():
        value = _field_value(text, labels)
        if value:
            value = re.sub(r"[^0-9A-Za-z가-힣]", "", value).lower()
        values[field] = value
    return values


def _same_contract(origin: Stage3Document, followup: Stage3Document) -> bool:
    if _company(origin) != _company(followup):
        return False
    origin_identity = _contract_identity(origin)
    followup_identity = _contract_identity(followup)
    name_matches = bool(origin_identity["contract_name"] and origin_identity["contract_name"] == followup_identity["contract_name"])
    if not name_matches:
        return False
    comparable = 0
    for field in ("counterparty", "amount", "date"):
        left = origin_identity[field]
        right = followup_identity[field]
        if left and right:
            if left != right:
                return False
            comparable += 1
    return comparable >= 1


def _correction_details(document: Stage3Document) -> tuple[list[str], str | None]:
    text = document.text or ""
    changed_fields: list[str] = []
    for field in ("정정사유", "정정 전", "정정 후", "변경 전", "변경 후"):
        if field in text:
            changed_fields.append(field)
    reason = _field_value(text, ("정정사유", "정정 사유"))
    return changed_fields, reason


def _link(origin: Stage3Document, followup: Stage3Document, relation: str, *, intent: Stage3Intent, confidence: float) -> dict[str, Any]:
    event: dict[str, Any] = {
        "relation": relation,
        "company": _company(origin) or None,
        "origin_document_id": origin.id,
        "followup_document_id": followup.id,
        "source_ids": [origin.id, followup.id],
        "confidence": confidence,
        "evidence": [origin.text[:300], followup.text[:300]],
    }
    if relation == "correction":
        changed_fields, reason = _correction_details(followup)
        event["correction_mode"] = intent.correction_mode or "latest_only"
        event["changed_fields"] = changed_fields
        event["correction_reason"] = reason
        if not changed_fields or not reason:
            event["status"] = "insufficient_evidence"
            event["warning"] = "정정 사유와 변경 필드를 확인할 수 없습니다."
        else:
            event["status"] = "linked"
    else:
        event["status"] = "linked"
    return event


def link_events(documents: Iterable[Stage3Document], intent: Stage3Intent) -> list[dict[str, Any]]:
    """Link only explicit or structurally exact disclosure events."""

    selected = _correction_documents(list(documents), intent)
    origins = [document for document in selected if _event_kind(document) == "origin"]
    base_documents = [document for document in selected if not _is_correction(document)]
    followups = [document for document in selected if _event_kind(document) in {"termination", "correction"}]
    links: list[dict[str, Any]] = []
    for followup in followups:
        explicit_id = _explicit_origin_id(followup)
        matching_origins = [
            origin
            for origin in origins
            if explicit_id and explicit_id in {origin.id, str(_all_metadata(origin).get("rcept_no", ""))}
        ]
        if not matching_origins and _is_correction(followup):
            matching_origins = [origin for origin in base_documents if _correction_key(origin) == _correction_key(followup)]
        if not matching_origins and _event_kind(followup) == "termination":
            matching_origins = [origin for origin in origins if _same_contract(origin, followup)]
        if not matching_origins:
            continue
        origin = max(matching_origins, key=lambda item: (str(_all_metadata(item).get("rcept_dt", "")), str(item.id)))
        relation = "correction" if _is_correction(followup) else "termination"
        confidence = 1.0 if explicit_id else (0.9 if relation == "correction" else 0.85)
        links.append(_link(origin, followup, relation, intent=intent, confidence=confidence))
    return links


def event_linker_agent(state: Stage3GraphState) -> AgentResult:
    intent = state["intent"]
    events = link_events(state.get("documents", []), intent)
    evidence_ids = tuple(
        dict.fromkeys(
            str(source_id)
            for event in events
            for source_id in event.get("source_ids", [])
            if source_id
        )
    )
    warnings = tuple(str(event["warning"]) for event in events if event.get("warning"))
    return AgentResult(
        agent="event_linker",
        status="ok" if events else "empty",
        linked_events=tuple(dict(event) for event in events),
        evidence_ids=evidence_ids,
        confidence=max((float(event.get("confidence", 0.0)) for event in events), default=0.0),
        warnings=warnings,
        trace=(f"linked_events={len(events)}",),
    )
