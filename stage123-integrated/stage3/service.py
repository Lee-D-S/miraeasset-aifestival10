from __future__ import annotations

from typing import Any, Mapping

from stage3.adapters.stage1 import adapt_stage1_intent
from stage3.agents.answer import AnswerWriter
from stage3.api_contract import to_submission_response
from stage3.contracts import Stage3Result
from stage3.orchestration.nodes import state_to_result
from stage3.orchestration.runtime import ExecutionMode, resolve_execution_mode
from stage3.orchestration.stdlib_runner import run_stdlib


class Stage3Service:
    """Public Stage3 facade backed by LangGraph or its stdlib fallback."""

    def __init__(self, *, answer_client: Any | None = None, execution_mode: str = "auto"):
        self.answer_writer = AnswerWriter(answer_client)
        self.execution_mode: ExecutionMode = resolve_execution_mode(execution_mode)
        self._langgraph_app: Any | None = None

    def process(self, *, question: str, stage1_intent: Mapping[str, Any], stage2_result: Any) -> Stage3Result:
        intent = adapt_stage1_intent(stage1_intent, question=question)
        try:
            if self.execution_mode == "langgraph":
                state = self._run_langgraph(question, intent, stage2_result)
            else:
                state = run_stdlib(
                    question=question,
                    intent=intent,
                    stage2_result=stage2_result,
                    answer_writer=self.answer_writer,
                )
            return state_to_result(state)
        except Exception as error:  # noqa: BLE001 - workflow boundary
            return Stage3Result(
                status="error",
                answer="Stage3 처리 중 오류가 발생해 답변을 검증할 수 없습니다.",
                warnings=[f"workflow_error: {type(error).__name__}"],
                trace=["workflow_failed"],
            )

    def _run_langgraph(self, question: str, intent: Any, stage2_result: Any) -> dict[str, Any]:
        if self._langgraph_app is None:
            from stage3.orchestration.langgraph_graph import build_graph

            self._langgraph_app = build_graph(answer_writer=self.answer_writer)
        return self._langgraph_app.invoke({
            "question": question,
            "intent": intent,
            "stage2_result": stage2_result,
            "warnings": [],
            "agent_results": [],
            "provenance": [],
            "handoffs": [],
            "trace": [],
            "fallback_used": False,
            "validation_warnings": [],
        })

    def answer(
        self,
        *,
        question_id: str,
        question: str,
        stage1_intent: Mapping[str, Any],
        stage2_result: Any,
    ) -> dict[str, str]:
        result = self.process(question=question, stage1_intent=stage1_intent, stage2_result=stage2_result)
        return to_submission_response(question_id, question, result)
