"""④-2 route 판단.

우선순위: unsafe > 코퍼스 범위 밖(unanswerable) > 모호·정보부족(need_clarify) > ok

"해당 조건의 공시가 0건"은 여기서 막지 않는다. 정상 질의인데 문서가 없는 경우는
2단계의 not_found로 처리해야 하고, 1단계에서 unanswerable로 찍으면 답할 수 있는
질의까지 죽는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..index.corpus_index import CorpusIndex, squash, squash_with_map
from .calculation import TWO_OPERAND_OPERATIONS, TWO_PERIOD_OPERATIONS
from .entity_linker import EntityResult
from .filter_builder import BuildResult
from .slot_extractor import SlotResult


@dataclass
class RouteDecision:
    route: str = "ok"
    reject_reason: Optional[str] = None
    clarify_message: Optional[str] = None
    missing_slots: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _guard_pattern_matches(
    text: str,
    pattern: str,
    *,
    allow_cross_token: bool = False,
) -> bool:
    """Match a guard without joining separate Korean tokens.

    The old implementation searched only the fully squashed question.  That
    made ``기사`` match the boundary between ``분기`` and ``사업``.  Direct
    matches still support patterns containing spaces; the squashed fallback
    is accepted only when the matched source characters were contiguous.
    """

    if pattern and pattern in text:
        return True

    if allow_cross_token:
        flexible = re.compile(r"\s*".join(re.escape(char) for char in pattern if not char.isspace()))
        if flexible.search(text):
            return True

    normalized = squash(pattern)
    if not normalized:
        return False
    squashed, positions = squash_with_map(text)
    start = squashed.find(normalized)
    while start >= 0:
        end = start + len(normalized)
        if all(
            positions[index + 1] == positions[index] + 1
            for index in range(start, end - 1)
        ):
            return True
        start = squashed.find(normalized, start + 1)
    return False


def scan_guards(text: str, index: CorpusIndex) -> Optional[RouteDecision]:
    """LLM 호출 전에 먼저 걸러야 하는 질의."""
    guards = index.config.guards

    for category, patterns in guards.get("unsafe", {}).items():
        for pattern in patterns:
            if _guard_pattern_matches(text, pattern, allow_cross_token=True):
                return RouteDecision(route="unsafe", reject_reason=f"unsafe:{category}")

    for category, patterns in guards.get("out_of_scope_topic", {}).items():
        for pattern in patterns:
            if _guard_pattern_matches(
                text,
                pattern,
                allow_cross_token=category != "external_source",
            ):
                return RouteDecision(
                    route="unanswerable", reject_reason=f"out_of_scope:{category}"
                )
    return None


def decide(
    pre_text: str,
    entities: EntityResult,
    slots: SlotResult,
    build: BuildResult,
    index: CorpusIndex,
) -> RouteDecision:
    guarded = scan_guards(pre_text, index)
    if guarded is not None:
        return guarded

    decision = RouteDecision()
    policy = index.config.defaults.get("clarify_policy", {})

    if not pre_text:
        decision.route = "need_clarify"
        decision.clarify_message = "질의가 비어 있습니다. 기업명과 확인하려는 항목을 알려주세요."
        return decision

    if entities.unknown_entities:
        names = ", ".join(entities.unknown_entities)
        decision.route = "unanswerable"
        decision.reject_reason = "out_of_universe_corp"
        decision.clarify_message = (
            f"{names}은 제공된 공시 코퍼스(70개사)에 포함되지 않아 확인할 수 없습니다."
        )
        return decision

    if build.out_of_range_years and not build.in_range_years:
        years = ", ".join(str(y) for y in build.out_of_range_years)
        decision.route = "unanswerable"
        decision.reject_reason = "out_of_corpus_year"
        decision.clarify_message = (
            f"제공된 공시는 2023년~2026년 1분기 범위입니다. {years}년은 확인할 수 없습니다."
        )
        return decision

    if build.out_of_range_years:
        decision.warnings.append(
            f"{', '.join(str(y) for y in build.out_of_range_years)}년은 코퍼스 범위 밖입니다. "
            "해당 수치는 범위 내 보고서의 비교표시 정보에서만 확인될 수 있습니다."
        )

    # 기간이 질의에 명시됐고 그 기간이 코퍼스에 없는 경우만 거절한다.
    if build.unavailable_periods and slots.period_explicit:
        periods = ", ".join(build.unavailable_periods)
        decision.route = "unanswerable"
        decision.reject_reason = "out_of_corpus_period"
        decision.clarify_message = f"{periods}는 제공된 공시에 포함되지 않아 확인할 수 없습니다."
        return decision

    if entities.ambiguous and policy.get("clarify_on_ambiguous_corp", True):
        first = entities.ambiguous[0]
        candidates = ", ".join(first["candidates"][:6])
        decision.route = "need_clarify"
        decision.reject_reason = "ambiguous_corp"
        decision.clarify_message = (
            f"'{first['token']}'만으로는 기업을 특정할 수 없습니다. "
            f"다음 중 어느 기업인지 알려주세요: {candidates}"
        )
        return decision

    has_target = bool(entities.corps or entities.sector)
    if not has_target and policy.get("clarify_on_missing_corp_and_sector", True):
        decision.route = "need_clarify"
        decision.reject_reason = "missing_corp"
        hint = ""
        if entities.suspect_entities:
            hint = f" ('{entities.suspect_entities[0]}'는 코퍼스에 없는 기업일 수 있습니다.)"
        decision.clarify_message = (
            "어느 기업의 공시를 확인할지 알려주세요." + hint
        )
        decision.missing_slots.append("corp_or_sector")
        return decision

    decision.missing_slots = _missing_slots(entities, slots, build)
    _apply_minimal_policy(decision, policy)
    return decision


def _missing_slots(entities: EntityResult, slots: SlotResult, build: BuildResult) -> list[str]:
    missing: list[str] = []
    target_count = len(entities.corps) + (len(entities.sector_members) if entities.sector else 0)

    if slots.intent == "compare" and slots.compare_axis != "period" and target_count < 2:
        missing.append("two_targets")
    operation = str(slots.calculation.get("operation") or "")
    if operation in TWO_PERIOD_OPERATIONS:
        needs_two_periods = True
    elif operation in TWO_OPERAND_OPERATIONS:
        # 대상이 2곳이면 값 2개가 채워지므로 기간은 하나로 충분하다.
        needs_two_periods = target_count < 2
    elif operation:
        needs_two_periods = False
    else:
        # 연산을 못 뽑은 calc·change 질의는 기존 기준을 유지한다.
        needs_two_periods = slots.intent in ("calc", "change")
    if needs_two_periods and len(slots.years) < 2 and not slots.prefer_latest:
        missing.append("two_periods")
    if not slots.metric:
        missing.append("metric")
    if not slots.years and not slots.prefer_latest:
        missing.append("time")
    return missing


def _apply_minimal_policy(decision: RouteDecision, policy: dict[str, Any]) -> None:
    """1-shot 평가라 역질문은 최소화한다. 나머지 결손은 경고로만 남긴다."""
    if policy.get("mode") != "minimal":
        if decision.missing_slots:
            decision.route = "need_clarify"
            decision.clarify_message = "질의에서 " + ", ".join(decision.missing_slots) + " 정보가 필요합니다."
        return

    if "time" in decision.missing_slots and policy.get("clarify_on_missing_time"):
        decision.route = "need_clarify"
        decision.clarify_message = "어느 기간(연도·보고서)을 기준으로 볼지 알려주세요."
        return
    if "metric" in decision.missing_slots and policy.get("clarify_on_missing_metric"):
        decision.route = "need_clarify"
        decision.clarify_message = "확인하려는 항목(예: 매출액, 설비투자)을 알려주세요."
        return

    for slot in decision.missing_slots:
        decision.warnings.append(f"슬롯 미확정: {slot} (2단계에서 범위를 넓혀 검색해야 합니다)")
