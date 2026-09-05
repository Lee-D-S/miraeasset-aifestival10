from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

from stage3.contracts import AgentResult, Stage3Fact, Stage3Intent
from stage3.state import Stage3GraphState
from stage3.grounding import matching_facts, period_matches, requested_aggregation_scope


logger = logging.getLogger(__name__)


def _citation_lines(citations: list[dict[str, Any]]) -> list[str]:
    return [
        f"- {item.get('source') or item.get('document_id', '')} ({item.get('document_id', '')})"
        for item in citations
    ]


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 1)
    except ValueError:
        return default


def _fact_debug_enabled() -> bool:
    return os.getenv("DIS164_DEBUG_FACTS", "false").strip().lower() in {"1", "true", "yes", "on"}


def _log_fact_debug(stage: str, facts: list[Stage3Fact]) -> None:
    if not _fact_debug_enabled():
        return
    payload = [
        {
            "document_id": fact.document_id,
            "kind": fact.kind,
            "metric": fact.metric,
            "label": fact.label,
            "company": fact.company,
            "value": fact.value,
            "unit": fact.unit,
            "period": fact.period,
            "basis": fact.basis,
            "aggregation_scope": fact.aggregation_scope,
            "table_context": fact.table_context,
        }
        for fact in facts[: _env_int("DIS164_DEBUG_FACT_LIMIT", 500)]
    ]
    logger.warning(
        "segment_fact_debug stage=%s count=%d facts=%s",
        stage,
        len(facts),
        json.dumps(payload, ensure_ascii=False, default=str),
    )


def _format_fact_value(fact: Stage3Fact) -> str:
    if fact.display_value:
        return str(fact.display_value)
    try:
        number = float(fact.value)
        if number.is_integer():
            integer = int(number)
            formatted = f"{integer:,}" if abs(integer) >= 10_000 else str(integer)
        else:
            formatted = f"{number:,.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        formatted = str(fact.value)
    return f"{formatted}{fact.unit or ''}"


def _index_like_amount(fact: Stage3Fact) -> bool:
    unit = str(fact.unit or "")
    if unit == "%":
        return True
    if unit in {"백만원", "원", "천원", "억원", "조원", "조"}:
        return False
    try:
        return abs(float(fact.value)) <= 1000
    except (TypeError, ValueError):
        return False


def _relevant_facts(intent: Stage3Intent, facts: list[Stage3Fact], limit: int | None = None) -> list[Stage3Fact]:
    """Prioritize facts matching the requested metric, period, and company."""
    requested_metrics = {str(intent.metric or "").strip().lower()} if intent.metric else set()
    for item in intent.query_plan:
        if isinstance(item, dict) and item.get("metric"):
            requested_metrics.add(str(item["metric"]).strip().lower())
    requested_metric = str(intent.metric or "").strip().lower()
    years = {str(year) for year in intent.time.get("years", [])} if isinstance(intent.time, dict) else set()
    companies = {str(company).strip().lower() for company in intent.companies if str(company).strip()}
    companies.update(str(company).strip().lower() for company in intent.manifest_filter.get("corp_names", []) if str(company).strip())
    basis = str(intent.basis or "").strip().lower()
    requested_scope = requested_aggregation_scope(intent)
    excluded_terms = {
        "revenue": ("매출채권", "매출원가", "매출총이익", "매출채권회전율"),
        "operating_profit": ("영업이익률",),
    }.get(requested_metric, ())
    grounded = matching_facts(facts, intent)

    def score(fact: Stage3Fact) -> tuple[int, float, str]:
        value = str(fact.value)
        fact_period = str(fact.period or "")
        fact_company = str(fact.company or "").strip().lower()
        fact_context = f"{fact.label} {fact.evidence}".lower()
        points = 0
        if grounded and fact in grounded:
            points += 400
        if excluded_terms and any(term in fact_context for term in excluded_terms):
            points -= 1_000
        if requested_metric in {"revenue", "operating_profit", "net_income"} and _index_like_amount(fact):
            points -= 1_000
        if requested_metrics and fact.metric.lower() in requested_metrics:
            points += 100
        if requested_metrics and any(metric in fact.label.lower() for metric in requested_metrics):
            points += 30
        if requested_metric == "revenue" and fact.label in {"매출액", "매출"}:
            points += 80
        if requested_metric == "operating_profit" and "영업이익" in fact.label:
            points += 80
        if years and any(year in fact_period for year in years):
            points += 20
        if companies and fact_company in companies:
            points += 20
        if basis and str(fact.basis or "").strip().lower() == basis:
            points += 10
        if fact.table_context:
            points += 10
            column_label = re.sub(r"\s+", "", str(fact.table_context.get("column_label") or ""))
            if "당" in column_label:
                points += 10
        if fact.kind != "numeric" or fact.aggregation_scope == requested_scope:
            points += 25
        elif requested_scope != "unknown":
            points -= 1_000
        if fact.kind != "date":
            points += 5
        if re.search(r"\d", value):
            points += 5
        try:
            magnitude = abs(float(fact.value)) if fact.kind != "date" else 0.0
        except (TypeError, ValueError):
            magnitude = 0.0
        if requested_metric in {"revenue", "operating_profit", "net_income", "assets", "liabilities", "equity"}:
            try:
                magnitude = abs(float(fact.normalized_value or fact.value))
            except (TypeError, ValueError):
                pass
        return (-points, -magnitude, fact.document_id)

    pool = grounded or facts
    fact_limit = limit if limit is not None else _env_int("CLOVA_PROMPT_FACT_LIMIT", 8)
    return sorted(pool, key=score)[:fact_limit]


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


def _compact_prompt_citations(citations: list[dict[str, Any]], facts: list[Stage3Fact]) -> list[dict[str, Any]]:
    """Keep only bounded evidence in the LLM prompt; preserve full result citations."""
    fact_ids = {fact.document_id for fact in facts if fact.document_id}
    selected = [item for item in citations if str(item.get("document_id", "")) in fact_ids]
    selected += [item for item in citations if item not in selected]
    citation_limit = _env_int("CLOVA_PROMPT_CITATION_LIMIT", 4)
    evidence_limit = _env_int("CLOVA_PROMPT_EVIDENCE_CHARS", 500)
    return [
        {
            "document_id": item.get("document_id"),
            "source": item.get("source"),
            "evidence": str(item.get("evidence", ""))[:evidence_limit],
        }
        for item in selected[:citation_limit]
    ]


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
    value = _format_fact_value(fact)
    citation = _citation_for_fact(fact, citations)
    source_line = ""
    if citation:
        document_id = str(citation.get("document_id", "")).strip()
        source = str(citation.get("source", "")).strip()
        source_line = f"\n\n출처: {source or '공시 문서'} [문서ID: {document_id}]"
    return f"결론\n{subject}의 {period} {basis} {metric}은 {value}입니다.{source_line}"


def _requested_years(intent: Stage3Intent) -> list[str]:
    time = intent.time if isinstance(intent.time, dict) else {}
    return [str(year) for year in time.get("years", []) if str(year).strip()]


def _lookup_facts(intent: Stage3Intent, facts: list[Stage3Fact]) -> list[Stage3Fact]:
    """Pick one Fact per requested year for multi-period lookups."""

    years = _requested_years(intent)
    pool = matching_facts(facts, intent) or facts
    if len(years) <= 1:
        return _relevant_facts(intent, pool, limit=1)
    selected: list[Stage3Fact] = []
    for year in years:
        bucket = [
            fact
            for fact in pool
            if period_matches(fact.period, year) or period_matches(fact.period, f"{year}-12")
        ]
        picked = _relevant_facts(intent, bucket, limit=1)
        if picked:
            selected.append(picked[0])
    return selected or _relevant_facts(intent, pool, limit=1)


def _segment_lookup_facts(intent: Stage3Intent, facts: list[Stage3Fact]) -> list[Stage3Fact]:
    """Return the requested metric for every available segment."""

    input_candidates = [
        fact
        for fact in facts
        if fact.metric == intent.metric
        or "매출" in str(fact.label)
        or "매출" in str(fact.evidence)
    ]
    _log_fact_debug("segment_input", input_candidates)
    pool = [fact for fact in matching_facts(facts, intent) if fact.unit != "%"]
    _log_fact_debug("segment_matching", pool)
    if not pool:
        return []
    # Segment questions commonly retrieve both a structured table cell and a
    # narrative sentence containing an unrelated number.  Prefer numeric,
    # table-backed facts whenever they exist so the narrative cannot win just
    # because it happens to match the metric and scope words.
    numeric_pool = [fact for fact in pool if fact.kind == "numeric"]
    table_numeric_pool = [fact for fact in numeric_pool if fact.table_context]
    if table_numeric_pool:
        pool = table_numeric_pool
    elif numeric_pool:
        pool = numeric_pool
    _log_fact_debug("segment_selected_pool", pool)
    unique: dict[tuple[str, str, str], Stage3Fact] = {}
    for fact in pool:
        row_label = str(fact.table_context.get("row_label") or fact.label or "")
        value = str(fact.normalized_value if fact.normalized_value is not None else fact.value)
        key = (row_label, str(fact.period or ""), value)
        unique.setdefault(key, fact)
    selected = sorted(
        unique.values(),
        key=lambda fact: (
            str(fact.table_context.get("row_label") or fact.label or ""),
            str(fact.period or ""),
            str(fact.document_id or ""),
        ),
    )[: _env_int("CLOVA_SEGMENT_FACT_LIMIT", 20)]
    _log_fact_debug("segment_final", selected)
    return selected


def _segment_lookup_claim(
    facts: list[Stage3Fact], intent: Stage3Intent, citations: list[dict[str, Any]]
) -> str:
    lines: list[str] = []
    for fact in facts:
        row_label = str(fact.table_context.get("row_label") or fact.label or "부문")
        lines.append(
            f"- {row_label}: {_format_fact_value(fact)} "
            f"({fact.period or '기간 미상'}, {fact.basis or '기준 미상'})"
        )
    return "결론\n사업부문별 매출\n" + "\n".join(lines)


def _selected_answer_facts(intent: Stage3Intent, facts: list[Stage3Fact]) -> list[Stage3Fact]:
    question_type = str(intent.question_type or intent.intent).lower()
    if question_type in {"lookup", "text", "exists"}:
        if requested_aggregation_scope(intent) == "segment":
            return _segment_lookup_facts(intent, facts)
        selected = _lookup_facts(intent, facts)
        extra = [fact for fact in _relevant_facts(intent, facts) if fact not in selected]
        return selected + extra[: max(0, 8 - len(selected))]
    return _relevant_facts(intent, facts)


def _multi_period_lookup_claim(
    facts: list[Stage3Fact], intent: Stage3Intent, citations: list[dict[str, Any]]
) -> str:
    subject = facts[0].company or (intent.companies[0] if intent.companies else "요청 기업")
    basis = facts[0].basis or intent.basis or "공시 기준"
    metric = facts[0].label or facts[0].metric or intent.metric or "요청 지표"
    lines = []
    for fact in facts:
        value = _format_fact_value(fact)
        lines.append(f"- {fact.period or '기간 미상'}: {value}")
    citation = _citation_for_fact(facts[-1], citations)
    source_line = ""
    if citation:
        document_id = str(citation.get("document_id", "")).strip()
        source = str(citation.get("source", "")).strip()
        source_line = f"\n\n출처: {source or '공시 문서'} [문서ID: {document_id}]"
    return f"결론\n{subject}의 {basis} {metric}\n" + "\n".join(lines) + source_line


def _format_calc_input(item: dict[str, Any]) -> str:
    period = item.get("period") or "기간 미상"
    value = item.get("value")
    unit = str(item.get("unit") or "")
    if isinstance(value, float) and value.is_integer():
        text = f"{int(value):,}"
    elif isinstance(value, (int, float)):
        text = f"{value:,.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"- {period}: {text}{unit}"


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
        selected_facts = _selected_answer_facts(intent, facts)
        prompt_citations = _compact_prompt_citations(citations, selected_facts)
        payload = {
            "question": question,
            "intent": intent.to_dict(),
            "facts": [fact.to_dict() for fact in selected_facts],
            "calculations": calculations,
            "comparisons": comparisons,
            "events": events,
            "citations": prompt_citations,
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
            _selected_answer_facts(intent, facts),
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
            series = calculation.get("series", [])
            if isinstance(series, list) and len(series) > 2:
                trend_lines = "\n".join(
                    f"- {item.get('period', '')}: {item.get('value', '')}{item.get('unit', '')}"
                    for item in series
                    if isinstance(item, dict)
                )
                if trend_lines:
                    sections.append(f"추이\n{trend_lines}")
            input_lines = []
            for item in calculation.get("inputs") or []:
                if isinstance(item, dict):
                    input_lines.append(_format_calc_input(item))
            result_line = f"{calculation['result']}{calculation.get('unit', '')}"
            if input_lines:
                sections.append("결론\n" + "\n".join(input_lines) + f"\n증감률 {result_line}\n\n계산식\n{calculation.get('formula', '')}")
            else:
                sections.append(f"결론\n{result_line}\n\n계산식\n{calculation.get('formula', '')}")
        else:
            facts_to_render = facts[:8]
            if requested_aggregation_scope(intent) == "segment":
                facts_to_render = _segment_lookup_facts(intent, facts)
            elif intent.query_plan:
                facts_to_render = facts[:8]
            elif str(intent.question_type or intent.intent).lower() in {"lookup", "text", "exists"}:
                facts_to_render = _lookup_facts(intent, facts)
            if facts_to_render and not intent.query_plan and str(intent.question_type or intent.intent).lower() in {"lookup", "text", "exists"}:
                if requested_aggregation_scope(intent) == "segment":
                    sections.append(_segment_lookup_claim(facts_to_render, intent, citations))
                elif len(facts_to_render) > 1:
                    sections.append(_multi_period_lookup_claim(facts_to_render, intent, citations))
                else:
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
        _log_fact_debug("answer_input", facts)
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
