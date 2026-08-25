from __future__ import annotations

import json
from typing import Any

from stage3.contracts import Stage3Fact, Stage3Intent


def _citation_lines(citations: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {item.get('source') or item.get('document_id', '')} ({item.get('document_id', '')})"
        for item in citations
    ]


class AnswerWriter:
    """Grounded answer writer with an optional HyperCLOVA X client."""

    def __init__(self, client: Any | None = None):
        self.client = client

    def write(
        self,
        *,
        question: str,
        intent: Stage3Intent,
        facts: list[Stage3Fact],
        calculations: list[dict[str, Any]],
        comparisons: list[dict[str, Any]],
        events: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        warnings: list[str],
    ) -> tuple[str, str]:
        payload = {
            "question": question,
            "intent": intent.to_dict(),
            "facts": [fact.to_dict() for fact in facts],
            "calculations": calculations,
            "comparisons": comparisons,
            "events": events,
            "citations": citations,
            "warnings": warnings,
        }
        if self.client is not None:
            prompt = (
                "제공된 공시 근거만 사용해 질문에 답하세요. 근거에 없는 수치나 사실을 만들지 마세요. "
                "계산 결과는 입력값과 산식을 따르고, 근거가 부족하면 확인할 수 없다고 답하세요.\n"
                "답변은 결론, 핵심 근거, 계산·비교 결과, 정보 한계 순서로 작성하세요.\n"
                f"자료:\n{json.dumps(payload, ensure_ascii=False)}"
            )
            return self.client.generate_text([{"role": "user", "content": prompt}]), "hyperclova_x"
        return self._template(intent, facts, calculations, comparisons, events, citations, warnings), "deterministic_template"

    @staticmethod
    def _template(intent: Stage3Intent, facts: list[Stage3Fact], calculations: list[dict[str, Any]], comparisons: list[dict[str, Any]], events: list[dict[str, Any]], citations: list[dict[str, Any]], warnings: list[str]) -> str:
        if not facts and not comparisons and not events:
            return "제공된 공시에서 질문에 필요한 근거를 확인할 수 없습니다."
        sections: list[str] = []
        if comparisons and comparisons[0].get("status") == "ok":
            top = comparisons[0]["top"]
            ranking = ", ".join(f"{item['rank']}위 {item['company']}({item['value']} {item['unit']})" for item in comparisons[0]["results"])
            sections.append(f"결론\n{top['company']}이(가) 가장 큽니다.\n\n비교 결과\n{ranking}")
        elif calculations and calculations[0].get("status") == "ok":
            calculation = calculations[0]
            sections.append(f"결론\n{calculation['result']}{calculation.get('unit', '')}\n\n계산식\n{calculation.get('formula', '')}")
        else:
            lines = [f"- {fact.label}: {fact.value} {fact.unit} ({fact.period or '기간 미상'}, {fact.basis or '기준 미상'})" for fact in facts[:8]]
            sections.append("핵심 근거\n" + "\n".join(lines))
        if events:
            sections.append("공시 이력\n" + "\n".join(f"- {event.get('relation')}: {event.get('source_ids')}" for event in events))
        if citations:
            sections.append("근거 공시\n" + "\n".join(_citation_lines(citations)))
        if warnings:
            sections.append("정보 한계\n" + "\n".join(f"- {warning}" for warning in warnings))
        return "\n\n".join(sections)
