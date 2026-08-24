from __future__ import annotations

import re
from typing import Any

from agentic_rag.contracts import AgentResult, Provenance
from agentic_rag.deterministic.calculations import percentage_change


_NUMBER = re.compile(r"(?P<label>매출|영업이익|당기순이익|자산)[^\d-]*(?P<value>-?\d+(?:[,.]\d+)?)")


def calculation_agent(state: dict[str, Any]) -> dict[str, Any]:
    values: list[float] = []
    evidence_ids: list[str] = []
    for document in state.get("cited_documents", []) or state.get("retrieved_documents", []):
        for match in _NUMBER.finditer(str(document.get("text", ""))):
            values.append(float(match.group("value").replace(",", "")))
            evidence_ids.append(str(document.get("id", "")))
    calculations: dict[str, Any] = {"values": values[:2], "formula": "(new-old)/abs(old)*100"}
    if len(values) >= 2:
        try:
            calculations["percentage_change"] = percentage_change(values[0], values[1])
        except ValueError as exc:
            calculations["error"] = str(exc)
    else:
        calculations["error"] = "계산에 필요한 두 수치를 찾지 못했습니다."
    confidence = 0.9 if len(values) >= 2 and "error" not in calculations else 0.2
    result = AgentResult("calculation", "ok" if confidence >= 0.9 else "insufficient", calculations=calculations, evidence_ids=tuple(evidence_ids), confidence=confidence, trace=(f"numeric_values={len(values)}",))
    provenance = Provenance("calculation", state.get("normalized_question", ""), tuple(evidence_ids), tuple(str(item.get("source", "")) for item in state.get("cited_documents", [])), confidence, {"calculation": calculations})
    return {"calculations": calculations, "agent_results": [result.as_dict()], "provenance": [provenance.as_dict()], "trace": list(result.trace)}

