"""1단계 진입점.

build_intent(question) -> Intent
반환값의 manifest_filter만으로 2단계가 문서를 고를 수 있어야 한다.
raw/ XML은 여기서 열지 않는다.
"""

from __future__ import annotations

from typing import Optional

from ..index.corpus_index import CorpusIndex
from ..models.intent import Intent
from . import filter_builder, router, slot_extractor, validator
from .calculation import build_calculation, canonical_question_type
from .entity_linker import link
from .preprocess import preprocess


def build_intent(
    question: str,
    index: CorpusIndex,
    use_llm: Optional[bool] = None,
    llm_client=None,
) -> Intent:
    pre = preprocess(question)

    guarded = router.scan_guards(pre.squashed, index)
    if guarded is not None:
        return _guard_only_intent(pre, guarded, index)

    entities = link(pre, index)
    slots = slot_extractor.extract(pre, entities, index)

    llm_used = False
    if _should_call_llm(use_llm, entities, slots, llm_client):
        from ..llm.slot_filler import fill_slots

        llm_used = fill_slots(pre, entities, slots, index, client=llm_client)

    if slots.derived_lookup_only:
        slots.intent = "lookup"
    # LLM이 기업·연도를 새로 채웠을 수 있어 비교 축을 다시 정한다.
    slots.compare_axis = slot_extractor.resolve_compare_axis(slots, entities)
    if not slots.calculation:
        slots.calculation = build_calculation(
            slots.intent,
            pre.text,
            slots.metric,
            denominator_metric=slots.derived_denominator,
            metric_matches=slots.metric_matches,
            compare_axis=slots.compare_axis,
            operation_override=slots.derived_operation,
        )
    slots.question_type = canonical_question_type(
        slots.intent,
        compare_axis=slots.compare_axis,
        operation=slots.calculation.get("operation"),
        metric=slots.metric,
    )

    build = filter_builder.build(entities, slots, index)
    decision = router.decide(pre.squashed, entities, slots, build, index)
    return validator.finalize(pre, entities, slots, build, decision, index, llm_used=llm_used)


def _should_call_llm(use_llm: Optional[bool], entities, slots, client=None) -> bool:
    """규칙으로 못 채운 칸이 있을 때만 LLM을 쓴다."""
    from ..llm.slot_filler import llm_enabled

    if use_llm is False or client is None:
        return False
    if use_llm is None and not llm_enabled():
        return False

    unresolved = (
        not entities.corps
        and not entities.sector
        or slots.metric is None
        or slots.intent == "unknown"
    )
    return bool(unresolved)


def _guard_only_intent(pre, decision, index: CorpusIndex) -> Intent:
    from .entity_linker import EntityResult
    from .filter_builder import BuildResult
    from .slot_extractor import SlotResult
    from ..models.intent import ManifestFilter

    return validator.finalize(
        pre,
        EntityResult(),
        SlotResult(),
        BuildResult(manifest_filter=ManifestFilter()),
        decision,
        index,
    )
