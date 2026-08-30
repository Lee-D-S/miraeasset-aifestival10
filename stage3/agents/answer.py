from __future__ import annotations

import json
import re
from typing import Any

from stage3.contracts import AgentResult, Stage3Fact, Stage3Intent
from stage3.state import Stage3GraphState


def _citation_lines(citations: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {item.get('source') or item.get('document_id', '')} ({item.get('document_id', '')})"
        for item in citations
    ]


def _relevant_facts(intent: Stage3Intent, facts: list[Stage3Fact], limit: int = 12) -> list[Stage3Fact]:
    """Prioritize facts matching the requested metric, period, and company."""
    requested_metric = str(intent.metric or "").strip().lower()
    years = {str(year) for year in intent.time.get("years", [])} if isinstance(intent.time, dict) else set()
    companies = {str(company).strip().lower() for company in intent.companies if str(company).strip()}
    companies.update(str(company).strip().lower() for company in intent.manifest_filter.get("corp_names", []) if str(company).strip())
    basis = str(intent.basis or "").strip().lower()

    def score(fact: Stage3Fact) -> tuple[int, float, str]:
        value = str(fact.value)
        fact_period = str(fact.period or "")
        fact_company = str(fact.company or "").strip().lower()
        points = 0
        if requested_metric and fact.metric.lower() == requested_metric:
            points += 100
        if requested_metric and requested_metric in fact.label.lower():
            points += 30
        if years and any(year in fact_period for year in years):
            points += 20
        if companies and fact_company in companies:
            points += 20
        if basis and str(fact.basis or "").strip().lower() == basis:
            points += 10
        if fact.kind != "date":
            points += 5
        if re.search(r"\d", value):
            points += 5
        try:
            magnitude = abs(float(fact.value)) if fact.kind != "date" else 0.0
        except (TypeError, ValueError):
            magnitude = 0.0
        return (-points, -magnitude, fact.document_id)

    return sorted(facts, key=score)[:limit]


def _has_required_claim(answer: str, facts: list[Stage3Fact], citations: list[dict[str, Any]]) -> bool:
    numeric_facts = [fact for fact in facts if fact.kind != "date" and re.search(r"\d", str(fact.value))]
    if numeric_facts:
        answer_digits = re.sub(r"\D", "", answer)
        if not any(re.sub(r"\D", "", str(fact.value).split(".", 1)[0]) in answer_digits for fact in numeric_facts[:3]):
            return False
    if citations and not any(str(item.get("document_id", "")) in answer for item in citations if item.get("document_id")):
        return False
    return bool(answer.strip())


def _citation_for_fact(fact: Stage3Fact, citations: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the citation that supports the primary fact when available."""

    for citation in citations:
        if str(citation.get("document_id", "")) == fact.document_id:
            return citation
    return citations[0] if citations else None


def _lookup_claim(fact: Stage3Fact, intent: Stage3Intent, citations: list[dict[str, Any]]) -> str:
    """Render a compact, explicit claim for semantic validation.

    A list of extracted facts is useful for debugging but is not a good answer:
    the semantic validator needs an unambiguous subject, period, metric, value,
    and citation.  This renderer deliberately does not invent a unit that the
    source parser did not provide.
    """

    subject = fact.company or (intent.companies[0] if intent.companies else "요청 기업")
    period = fact.period or ", ".join(
        str(year) for year in intent.time.get("years", [])
    ) or "요청 기간"
    basis = fact.basis or intent.basis or "공시 기준"
    metric = fact.label or fact.metric or intent.metric or "요청 지표"
    value = f"{fact.value}{fact.unit}".strip()
    citation = _citation_for_fact(fact, citations)
    source_line = ""
    if citation:
        document_id = str(citation.get("document_id", "")).strip()
        source = str(citation.get("source", "")).strip()
        source_line = f"\n\n출처: {source or '공시 문서'} [문서ID: {document_id}]"
    return f"결론\n{subject}의 {period} {basis} {metric}은 {value}입니다.{source_line}"


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
        selected_facts = _relevant_facts(intent, facts)
        payload = {
            "question": question,
            "intent": intent.to_dict(),
            "facts": [fact.to_dict() for fact in selected_facts],
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
            answer = self.client.generate_text([{"role": "user", "content": prompt}])
            if getattr(self.client, "strict_grounding", False) and not _has_required_claim(answer, selected_facts, citations):
                return self._template(intent, selected_facts, calculations, comparisons, events, citations, warnings), "deterministic_grounding_fallback"
            return answer, "hyperclova_x"
        return self._template(intent, selected_facts, calculations, comparisons, events, citations, warnings), "deterministic_template"

    @staticmethod
    def deterministic(
        *,
        intent: Stage3Intent,
        facts: list[Stage3Fact],
        calculations: list[dict[str, Any]],
        comparisons: list[dict[str, Any]],
        events: list[dict[str, Any]],
        citations: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Render the local fallback without calling an external model."""

        return AnswerWriter._template(
            intent,
            _relevant_facts(intent, facts),
            calculations,
            comparisons,
            events,
            citations,
            warnings,
        )

    @staticmethod
    def _template(intent: Stage3Intent, facts: list[Stage3Fact], calculations: list[dict[str, Any]], comparisons: list[dict[str, Any]], events: list[dict[str, Any]], citations: list[dict[str, Any]], warnings: list[str]) -> str:
        if not facts and not comparisons and not events:
            return "제공된 공시에서 질문에 필요한 근거를 확인할 수 없습니다."
        if calculations and not any(item.get("status") == "ok" for item in calculations):
            return "계산에 필요한 기간·단위·기준을 공시 근거에서 확인할 수 없습니다."
        if comparisons and not any(item.get("status") == "ok" for item in comparisons):
            return "비교에 필요한 동일 기간·단위·기준의 공시 근거를 확인할 수 없습니다."
        sections: list[str] = []
        if comparisons and comparisons[0].get("status") == "ok":
            top = comparisons[0]["top"]
            ranking = ", ".join(f"{item['rank']}위 {item['company']}({item['value']} {item['unit']})" for item in comparisons[0]["results"])
            sections.append(f"결론\n{top['company']}이(가) 가장 큽니다.\n\n비교 결과\n{ranking}")
        elif calculations and calculations[0].get("status") == "ok":
            calculation = calculations[0]
            sections.append(f"결론\n{calculation['result']}{calculation.get('unit', '')}\n\n계산식\n{calculation.get('formula', '')}")
        else:
            facts_to_render = facts[:8]
            if str(intent.question_type or intent.intent).lower() in {"lookup", "text", "exists"}:
                facts_to_render = facts[:1]
            if facts_to_render and str(intent.question_type or intent.intent).lower() in {"lookup", "text", "exists"}:
                sections.append(_lookup_claim(facts_to_render[0], intent, citations))
            else:
                lines = [f"- {fact.label}: {fact.value} {fact.unit} ({fact.period or '기간 미상'}, {fact.basis or '기준 미상'})" for fact in facts_to_render]
                sections.append("핵심 근거\n" + "\n".join(lines))
        if events:
            sections.append("공시 이력\n" + "\n".join(f"- {event.get('relation')}: {event.get('source_ids')}" for event in events))
        if citations:
            sections.append("근거 공시\n" + "\n".join(_citation_lines(citations)))
        if warnings:
            sections.append("정보 한계\n" + "\n".join(f"- {warning}" for warning in warnings))
        return "\n\n".join(sections)


def make_answer_agent(writer: AnswerWriter):
    def answer_agent(state: Stage3GraphState) -> AgentResult:
        intent = state["intent"]
        facts = [
            fact if isinstance(fact, Stage3Fact) else Stage3Fact.from_dict(fact)
            for fact in state.get("facts", [])
        ]
        answer, mode = writer.write(
            question=state.get("question", intent.question),
            intent=intent,
            facts=facts,
            calculations=list(state.get("calculations", [])),
            comparisons=list(state.get("comparison_results", [])),
            events=list(state.get("linked_events", [])),
            citations=list(state.get("citations", [])),
            warnings=list(state.get("warnings", [])),
        )
        return AgentResult(
            agent="answer",
            status="ok" if answer.strip() else "empty",
            answer=answer,
            evidence_ids=tuple(
                str(item.get("document_id", ""))
                for item in state.get("citations", [])
                if item.get("document_id")
            ),
            confidence=0.9 if answer.strip() else 0.0,
            trace=(f"mode={mode}",),
        )

    return answer_agent
