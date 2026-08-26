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
from .entity_linker import link
from .preprocess import preprocess


def build_intent(
    question: str,
    index: CorpusIndex,
    use_llm: Optional[bool] = None,
) -> Intent:
    pre = preprocess(question)

    guarded = router.scan_guards(pre.squashed, index)
    if guarded is not None:
        return _guard_only_intent(pre, guarded, index)

    entities = link(pre, index)
    slots = slot_extractor.extract(pre, entities, index)

    llm_used = False
    if _should_call_llm(use_llm, entities, slots):
        from ..llm.slot_filler import fill_slots

        llm_used = fill_slots(pre, entities, slots, index)

    build = filter_builder.build(entities, slots, index)
    decision = router.decide(pre.squashed, entities, slots, build, index)
    return validator.finalize(pre, entities, slots, build, decision, index, llm_used=llm_used)


def _should_call_llm(use_llm: Optional[bool], entities, slots) -> bool:
    """규칙으로 못 채운 칸이 있을 때만 LLM을 쓴다."""
    from ..llm.slot_filler import llm_enabled

    if use_llm is False:
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
