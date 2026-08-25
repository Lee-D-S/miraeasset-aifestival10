from __future__ import annotations

from typing import Any, Mapping

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.adapters.stage2 import adapt_stage2_bundle
from stage3.agents.answer import AnswerWriter
from stage3.agents.calculation import calculate_facts
from stage3.agents.comparison import compare_facts
from stage3.agents.event_linker import link_events
from stage3.agents.fact_extraction import extract_facts
from stage3.api_contract import to_submission_response
from stage3.contracts import Stage3Result
from stage3.deterministic.normalization import normalize_facts
from stage3.validation import validate_stage3_result


class Stage3Service:
    """Supervisor for Stage1 input plus Stage2 evidence."""

    def __init__(self, *, answer_client: Any | None = None):
        self.answer_writer = AnswerWriter(answer_client)

    def process(self, *, question: str, stage1_intent: Mapping[str, Any], stage2_result: Any) -> Stage3Result:
        intent = adapt_stage1_intent(stage1_intent, question=question)
        if not intent.is_processable:
            answer = self._blocked_answer(intent.route)
            result = Stage3Result(status=intent.route, answer=answer, warnings=list(intent.warnings), trace=[f"route={intent.route}"])
            return result

        bundle = adapt_stage2_bundle(stage2_result)
        documents = bundle.effective_documents()
        facts = extract_facts(documents, intent)
        facts, normalization_warnings = normalize_facts(facts, intent)
        calculations: list[dict[str, Any]] = []
        comparisons: list[dict[str, Any]] = []
        if intent.intent in {"calc", "calculation"} or any(word in intent.normalized_question for word in ("증감률", "증가율", "비중", "합계", "성장률")):
            calculations.append(calculate_facts(facts, intent))
        if intent.intent in {"compare", "comparison"} or any(word in intent.normalized_question for word in ("비교", "어느 기업", "가장 큰", "순위")):
            comparisons.append(compare_facts(facts, intent))
        events = link_events(documents, intent) if intent.intent in {"exists", "event_link", "change"} or any(word in intent.normalized_question for word in ("계약", "해지", "정정", "후속")) else []
        citations = self._citations(documents, facts, calculations, comparisons, events)
        warnings = list(intent.warnings) + normalization_warnings + self._document_warnings(documents)
        answer, mode = self.answer_writer.write(question=question, intent=intent, facts=facts, calculations=calculations, comparisons=comparisons, events=events, citations=citations, warnings=warnings)
        status = "success" if documents and (facts or comparisons or events) else "insufficient_evidence"
        result = Stage3Result(
            status=status,
            answer=answer,
            facts=[fact.to_dict() for fact in facts],
            calculations=calculations,
            comparison_results=comparisons,
            linked_events=events,
            citations=citations,
            warnings=warnings,
            provenance=[{"agent": "stage3_supervisor", "documents": [document.id for document in documents], "answer_mode": mode}],
            trace=["stage1_adapted", f"documents={len(documents)}", f"facts={len(facts)}", f"answer_mode={mode}"],
        )
        valid, validation_warnings = validate_stage3_result(result, intent)
        if not valid:
            result = Stage3Result(**{**result.to_dict(), "status": "insufficient_evidence", "warnings": [*result.warnings, *validation_warnings], "trace": [*result.trace, "validation_failed"]})
        return result

    def answer(self, *, question_id: str, question: str, stage1_intent: Mapping[str, Any], stage2_result: Any) -> dict[str, str]:
        result = self.process(question=question, stage1_intent=stage1_intent, stage2_result=stage2_result)
        return to_submission_response(question_id, question, result)

    @staticmethod
    def _blocked_answer(route: str) -> str:
        return {
            "need_clarify": "질문의 기업·기간·기준이 명확하지 않습니다.",
            "unanswerable": "제공된 공시 코퍼스에서 확인할 수 없는 질문입니다.",
            "unsafe": "공시 근거만으로 답변할 수 없는 요청입니다.",
        }.get(route, "제공된 공시에서 확인할 수 없습니다.")

    @staticmethod
    def _document_warnings(documents: list[Any]) -> list[str]:
        warnings: list[str] = []
        for document in documents:
            file_format = str(document.metadata.get("file_format", "")).lower()
            span_text = any(
                str(span.get("text", span.get("content", span.get("evidence", "")))).strip()
                for span in document.evidence_spans
            )
            if "pdf" in file_format and not document.text.strip() and not span_text:
                warnings.append(f"{document.id}: pdf_text_required")
        return warnings

    @staticmethod
    def _citations(documents: list[Any], facts: list[Any], calculations: list[dict[str, Any]], comparisons: list[dict[str, Any]], events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        used: set[str] = {fact.document_id for fact in facts}
        for calculation in calculations:
            used.update(map(str, calculation.get("evidence_ids", [])))
        for comparison in comparisons:
            used.update(map(str, comparison.get("evidence_ids", [])))
        for event in events:
            used.update(map(str, event.get("source_ids", [])))
        return [
            {"document_id": document.id, "source": document.source, "evidence": document.text, "metadata": document.metadata}
            for document in documents if document.id in used
        ]
