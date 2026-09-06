from __future__ import annotations

from typing import Any, Mapping

from reasoner.adapters.interpreter import adapt_interpreter_intent
from reasoner.agents.answer import AnswerWriter
from reasoner.api_contract import to_submission_response
from reasoner.contracts import ReasonerResult
from reasoner.node import build_reasoner_node
from reasoner.orchestration.runtime import ExecutionMode, resolve_execution_mode


class ReasonerService:
    """Public Reasoner facade backed by LangGraph or its stdlib fallback."""

    def __init__(self, *, answer_client: Any | None = None, execution_mode: str = "auto"):
        self.answer_writer = AnswerWriter(answer_client)
        self.execution_mode: ExecutionMode = resolve_execution_mode(execution_mode)
        self._reasoner_node = build_reasoner_node(answer_writer=self.answer_writer)
        self._langgraph_app: Any | None = None

    def process(self, *, question: str, interpreter_intent: Mapping[str, Any], retriever_result: Any) -> ReasonerResult:
        intent = adapt_interpreter_intent(interpreter_intent, question=question)
        try:
            if self.execution_mode == "langgraph":
                state = self._run_langgraph(question, intent, retriever_result)
            else:
                state = self._reasoner_node({
                    "question": question,
                    "intent": intent,
                    "route": intent.route,
                    "retriever_result": retriever_result,
                    "context": "",
                    "messages": [],
                    "gen_retry_num": 0,
                })
            return ReasonerResult.from_dict(state["reasoner_result"])
        except Exception as error:  # noqa: BLE001 - workflow boundary
            return ReasonerResult(
                status="error",
                answer="Reasoner 처리 중 오류가 발생해 답변을 검증할 수 없습니다.",
                warnings=[f"workflow_error: {type(error).__name__}"],
                trace=["workflow_failed"],
            )

    def _run_langgraph(self, question: str, intent: Any, retriever_result: Any) -> dict[str, Any]:
        if self._langgraph_app is None:
            from reasoner.orchestration.langgraph_graph import build_graph

            self._langgraph_app = build_graph(answer_writer=self.answer_writer)
        return self._langgraph_app.invoke({
            "question": question,
            "intent": intent,
            "retriever_result": retriever_result,
            "route": intent.route,
            "context": "",
            "messages": [],
            "gen_retry_num": 0,
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
        interpreter_intent: Mapping[str, Any],
        retriever_result: Any,
    ) -> dict[str, str]:
        result = self.process(question=question, interpreter_intent=interpreter_intent, retriever_result=retriever_result)
        return to_submission_response(question_id, question, result)
