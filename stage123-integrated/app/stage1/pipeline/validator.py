"""⑤ 검증·정규화 — Intent를 확정하고 manifest로 후보 문서 건수를 확인한다."""

from __future__ import annotations

from typing import Optional

from ..index.corpus_index import CorpusIndex
from ..models.intent import DOC_GROUPS, DOC_SUBTYPES, Intent, ManifestFilter, TimeSpec
from .entity_linker import EntityResult
from .filter_builder import BuildResult
from .preprocess import PreprocessResult
from .router import RouteDecision
from .slot_extractor import SlotResult

_SEARCHABLE_ROUTES = {"ok"}


def finalize(
    pre: PreprocessResult,
    entities: EntityResult,
    slots: SlotResult,
    build: BuildResult,
    decision: RouteDecision,
    index: CorpusIndex,
    llm_used: bool = False,
) -> Intent:
    flt = build.manifest_filter
    _validate_enums(flt)

    intent = Intent(
        raw_question=pre.raw,
        normalized_question=pre.text,
        intent=slots.intent,
        route=decision.route,
        corps=entities.corps,
        sector=entities.sector,
        sector_members=entities.sector_members,
        ambiguous_mentions=entities.ambiguous,
        unknown_entities=entities.unknown_entities,
        metric=slots.metric,
        metric_confidence=slots.metric_confidence,
        basis=slots.basis,
        time=TimeSpec(
            mode=slots.time_mode,
            years=list(slots.years),
            doc_subtype=flt.doc_subtype,
            base_months=list(flt.base_months),
            prefer_latest=slots.prefer_latest,
            relative_terms=list(slots.relative_terms),
            out_of_range_years=list(build.out_of_range_years),
        ),
        correction_mode=slots.correction_mode,
        allow_pdf_html=bool(index.config.defaults.get("defaults", {}).get("allow_pdf_html", True)),
        manifest_filter=flt,
        assumptions=list(build.assumptions),
        warnings=list(build.warnings) + list(decision.warnings),
        missing_slots=list(decision.missing_slots),
        reject_reason=decision.reject_reason,
        clarify_message=decision.clarify_message,
        llm_used=llm_used,
    )

    if intent.route in _SEARCHABLE_ROUTES:
        intent.doc_count = index.count_docs(flt)
        intent.availability = _availability(intent.doc_count, entities, flt)
        if intent.doc_count == 0:
            intent.warnings.append(
                "현재 필터로 후보 문서가 0건입니다. 2단계에서 조건을 완화하거나 "
                "'공시에서 확인되지 않음'으로 응답해야 합니다."
            )
    else:
        # 검색으로 넘기지 않는 질의는 필터를 비워, 2단계가 실수로 조회하지 않게 한다.
        intent.manifest_filter = ManifestFilter()
        intent.availability = "not_searched"

    if intent.sector and not intent.corps:
        intent.assumptions.append(
            f"기업이 특정되지 않아 섹터 '{intent.sector}' {len(intent.sector_members)}개사를 후보로 넘깁니다."
        )

    return intent


def _validate_enums(flt: ManifestFilter) -> None:
    if flt.doc_group and flt.doc_group not in DOC_GROUPS:
        raise ValueError(f"알 수 없는 doc_group: {flt.doc_group}")

    flt.doc_group_candidates = [g for g in flt.doc_group_candidates if g in DOC_GROUPS]

    groups = flt.effective_doc_groups()
    allowed: set[str] = set()
    for group in groups or DOC_GROUPS:
        allowed.update(DOC_SUBTYPES.get(group, ()))

    if flt.doc_subtype and allowed and flt.doc_subtype not in allowed:
        flt.doc_subtype = None
    flt.doc_subtype_candidates = [s for s in flt.doc_subtype_candidates if not allowed or s in allowed]

    # base_year / base_month는 정기공시에만 있는 필드다.
    if flt.doc_group in ("major", "exchange", "holding"):
        flt.base_years = []
        flt.base_months = []


def _availability(doc_count: int, entities: EntityResult, flt: ManifestFilter) -> str:
    if doc_count > 0:
        return "available"
    if not flt.corp_names and not flt.sector:
        return "unknown"
    return "empty"


def summarize(intent: Intent) -> str:
    return intent.trace_summary()
