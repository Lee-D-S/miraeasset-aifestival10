"""Bounded Supervisor decisions and tool contracts for the Stage graph."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Literal, Protocol


SupervisorAction = Literal[
    "run_stage2",
    "retry_search",
    "run_calculation_planner",
    "run_stage3",
    "run_stage4",
    "request_clarification",
    "unanswerable",
    "fail_closed",
    "regenerate_answer",
    "finish",
]
SupervisorPhase = Literal["after_stage1", "after_stage2", "after_stage3", "after_stage4"]
ALLOWED_ACTIONS = frozenset({
    "run_stage2", "retry_search", "run_calculation_planner", "run_stage3",
    "run_stage4", "request_clarification", "unanswerable", "fail_closed",
    "regenerate_answer", "finish",
})


@dataclass(frozen=True)
class SupervisorDecision:
    action: SupervisorAction
    reason: str = ""


class SupervisorClient(Protocol):
    """LLM adapter. Production code can implement this without changing graph code."""

    def decide(self, *, phase: SupervisorPhase, state: Mapping[str, Any]) -> SupervisorDecision | Mapping[str, Any]: ...


class StructuredSupervisorClient:
    """Adapter for a chat model that returns only an action JSON object."""

    def __init__(self, model: Any):
        self.model = model

    def decide(self, *, phase: SupervisorPhase, state: Mapping[str, Any]) -> SupervisorDecision:
        prompt = {
            "phase": phase,
            "state": {key: value for key, value in state.items() if key not in {"messages", "question"}},
            "allowed_actions": sorted(ALLOWED_ACTIONS),
        }
        response = self.model.invoke([
            {"role": "system", "content": "Return JSON only: {\\\"action\\\": allowed_action, \\\"reason\\\": string}. Do not modify state."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)},
        ])
        content = response if isinstance(response, Mapping) else getattr(response, "content", response)
        if isinstance(content, list):
            content = "".join(str(item.get("text", item)) if isinstance(item, Mapping) else str(item) for item in content)
        payload = json.loads(str(content))
        return normalize_decision(payload)


def _intent(state: Mapping[str, Any]) -> Mapping[str, Any]:
    value = state.get("intent")
    return value if isinstance(value, Mapping) else {}


def _calculation_plan_missing(state: Mapping[str, Any]) -> bool:
    """Backward-compatible name for the canonical-plan requirement check."""

    return _analysis_plan_required(state)


def _analysis_plan_required(state: Mapping[str, Any]) -> bool:
    intent = _intent(state)
    question_type = str(intent.get("question_type", intent.get("intent", ""))).lower()
    plan = state.get("analysis_plan")
    if isinstance(plan, Mapping) and plan.get("status") == "ready":
        return False
    if question_type in {"calculation", "calc"}:
        return True
    question = str(state.get("question", intent.get("normalized_question", ""))).replace(" ", "")
    query_plan = intent.get("query_plan")
    has_multiple_requirements = isinstance(query_plan, list) and len(query_plan) > 1
    has_derived_comparison = (
        has_multiple_requirements
        and any(term in question for term in ("비중", "비율"))
        and any(term in question for term in ("전년", "증가", "감소", "변화", "증감"))
        and any(term in question for term in ("중", "가장", "최대", "최소"))
    )
    return has_derived_comparison


class DeterministicSupervisor:
    """Safe default policy used when no LLM adapter is injected."""

    def __init__(
        self,
        *,
        max_search_retries: int = 1,
        max_planner_retries: int = 1,
        allow_regeneration: bool = True,
    ):
        self.max_search_retries = max_search_retries
        self.max_planner_retries = max_planner_retries
        self.allow_regeneration = allow_regeneration

    def decide(self, *, phase: SupervisorPhase, state: Mapping[str, Any]) -> SupervisorDecision:
        route = str(state.get("route", ""))
        if phase == "after_stage1":
            if route == "unsafe":
                return SupervisorDecision("fail_closed", "Stage1이 안전하지 않은 질의로 분류했습니다.")
            if route == "need_clarify":
                return SupervisorDecision("request_clarification", "Stage1에 필수 질의 슬롯이 없습니다.")
            if route == "unanswerable":
                return SupervisorDecision("unanswerable", "Stage1에서 처리 불가로 분류했습니다.")
            if _analysis_plan_required(state):
                attempts = int(state.get("planner_attempts", 0) or 0)
                if attempts >= self.max_planner_retries:
                    return SupervisorDecision("fail_closed", "계산 계획을 제한된 횟수 안에 만들지 못했습니다.")
                return SupervisorDecision("run_calculation_planner", "계산 계획이 없습니다.")
            return SupervisorDecision("run_stage2", "정상 검색을 시작합니다.")

        if phase == "after_stage2":
            result = state.get("stage2_result")
            result = result if isinstance(result, Mapping) else {}
            if result.get("cited_documents"):
                return SupervisorDecision("run_stage3", "인용 가능한 근거가 있습니다.")
            if int(state.get("retry_num", 0) or 0) < self.max_search_retries:
                return SupervisorDecision("retry_search", "검색 결과가 부족합니다.")
            return SupervisorDecision("unanswerable", "제한된 검색 재시도 후에도 문서가 없습니다.")

        if phase == "after_stage3":
            result = state.get("stage3_result")
            result = result if isinstance(result, Mapping) else {}
            if _analysis_plan_required(state) and int(state.get("planner_attempts", 0) or 0) < self.max_planner_retries:
                return SupervisorDecision("run_calculation_planner", "Stage3에 계산 계획이 필요합니다.")
            if result.get("status") in {"success", "partial_success"}:
                return SupervisorDecision("run_stage4", "분석 결과가 생성되었습니다.")
            return SupervisorDecision("fail_closed", "분석 결과를 근거로 검증할 수 없습니다.")

        result = state.get("stage4_result")
        result = result if isinstance(result, Mapping) else {}
        if result.get("status") == "validation_failed":
            if self.allow_regeneration and int(state.get("regeneration_attempts", 0) or 0) < 1:
                return SupervisorDecision("regenerate_answer", "검증 실패를 제한된 1회 재생성으로 보완합니다.")
            return SupervisorDecision("fail_closed", "재생성 모델이 없거나 재생성 한도를 초과했습니다.")
        return SupervisorDecision("finish", "Stage4 처리가 완료되었습니다.")


def normalize_decision(value: SupervisorDecision | Mapping[str, Any]) -> SupervisorDecision:
    if isinstance(value, SupervisorDecision):
        if value.action not in ALLOWED_ACTIONS:
            raise ValueError(f"허용되지 않은 Supervisor action입니다: {value.action}")
        return value
    if not isinstance(value, Mapping):
        raise ValueError("Supervisor decision은 mapping이어야 합니다.")
    action = str(value.get("action", "fail_closed"))
    if action not in ALLOWED_ACTIONS:
        raise ValueError(f"허용되지 않은 Supervisor action입니다: {action}")
    return SupervisorDecision(action, str(value.get("reason", "")))  # type: ignore[arg-type]


def build_supervisor_node(
    client: SupervisorClient | None = None,
    *,
    max_supervisor_steps: int = 12,
    max_planner_retries: int = 1,
    allow_regeneration: bool = True,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    policy = client or DeterministicSupervisor(
        max_planner_retries=max_planner_retries,
        allow_regeneration=allow_regeneration,
    )

    def supervisor_node(state: Mapping[str, Any]) -> dict[str, Any]:
        phase = str(state.get("supervisor_phase", "after_stage1"))
        if phase not in {"after_stage1", "after_stage2", "after_stage3", "after_stage4"}:
            decision = SupervisorDecision("fail_closed", "알 수 없는 Supervisor phase입니다.")
        elif int(state.get("supervisor_steps", 0) or 0) >= max_supervisor_steps:
            decision = SupervisorDecision("fail_closed", "Supervisor 최대 단계 수를 초과했습니다.")
        else:
            try:
                decision = normalize_decision(policy.decide(phase=phase, state=state))  # type: ignore[arg-type]
            except Exception as error:  # LLM boundary: fail closed
                decision = SupervisorDecision("fail_closed", f"Supervisor 오류: {type(error).__name__}")
        return {
            "supervisor_steps": int(state.get("supervisor_steps", 0) or 0) + 1,
            "supervisor_action": decision.action,
            "supervisor_reason": decision.reason,
            "phase": phase,
            "next_action": decision.action,
            "last_action": decision.action,
        }

    return supervisor_node


def build_planner_tool(planner: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None):
    """Return the single canonical calculation planner graph adapter."""

    if planner is None:
        from stage3.deterministic.calculation_planner import build_state_analysis_plan

        planner = build_state_analysis_plan

    def calculation_planner(state: Mapping[str, Any]) -> dict[str, Any]:
        update = dict(planner(state))
        return {
            **update,
            "planner_retry_num": int(state.get("planner_retry_num", 0) or 0) + 1,
            "planner_attempts": int(state.get("planner_attempts", 0) or 0) + 1,
        }

    return calculation_planner


def retry_search_tool(state: Mapping[str, Any]) -> dict[str, Any]:
    current_query = str(state.get("search_query") or state.get("question") or "")
    original = str(state.get("original_question") or state.get("question") or "")
    retry = int(state.get("retry_num", 0) or 0) + 1
    # A retry must alter the retrieval query while retaining the original
    # question as an immutable audit field.
    query = f"{current_query} 관련 핵심 공시 근거" if retry % 2 else f"{original} 수치 기간 기준 출처"
    return {
        "retry_num": retry,
        "search_attempts": int(state.get("search_attempts", 0) or 0) + 1,
        "search_query": query,
    }


__all__ = [
    "DeterministicSupervisor",
    "SupervisorAction",
    "SupervisorClient",
    "SupervisorDecision",
    "StructuredSupervisorClient",
    "ALLOWED_ACTIONS",
    "build_planner_tool",
    "build_supervisor_node",
    "normalize_decision",
    "retry_search_tool",
]
