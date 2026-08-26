from __future__ import annotations

import json
import operator
import uuid
from typing import Any, Annotated, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from app.schemas.dbquery_args import DbQueryArgs
from app.tools.hybrid_db_tools import configure_repository, dart_hybrid_search_tool
from app.tools.math_tools import calculator


TOOLS = [dart_hybrid_search_tool, calculator]


class Stage2AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    original_question: str
    intent: dict[str, Any]
    search_query: str
    search_attempts: int
    stage2_result: dict[str, Any]
    context: str
    trace: Annotated[list[str], operator.add]
    warnings: Annotated[list[str], operator.add]


def _last_tool_message(messages: list[BaseMessage]) -> ToolMessage | None:
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            return message
    return None


def _payload(content: Any) -> dict[str, Any]:
    if isinstance(content, dict):
        return dict(content)
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _filters(intent: dict[str, Any]) -> dict[str, Any]:
    manifest = dict(intent.get("manifest_filter") or {})
    corp_names = []
    for item in intent.get("corps", []) or []:
        if isinstance(item, dict) and item.get("corp_name"):
            corp_names.append(str(item["corp_name"]))
        elif str(item).strip():
            corp_names.append(str(item))
    return {
        "corp_names": corp_names or list(manifest.get("corp_names") or []),
        "sector": intent.get("sector") or manifest.get("sector"),
        "doc_group": manifest.get("doc_group"),
        "doc_group_candidates": list(manifest.get("doc_group_candidates") or []),
        "doc_subtype": manifest.get("doc_subtype"),
        "doc_subtype_candidates": list(manifest.get("doc_subtype_candidates") or []),
        "base_years": list(manifest.get("base_years") or []),
        "base_months": list(manifest.get("base_months") or []),
        "start_date": manifest.get("rcept_from"),
        "end_date": manifest.get("rcept_to"),
        "is_correction": manifest.get("is_correction", False),
        "basis": intent.get("basis"),
        "report_nm_contains": list(manifest.get("report_nm_contains") or []),
    }


def _merge_stage1_filters(tool_call: dict[str, Any], state: Stage2AgentState) -> dict[str, Any]:
    args = dict(tool_call.get("args") or {})
    # Stage1's manifest is authoritative. Keep only the two fields Stage2 may
    # choose freely; otherwise an LLM-provided date/sector filter can silently
    # make a valid periodic document set look empty.
    args = {key: value for key, value in args.items() if key in {"query", "top_k"}}
    args.setdefault("query", state.get("search_query") or state.get("original_question", ""))
    for key, value in _filters(state.get("intent", {})).items():
        if value not in (None, [], ""):
            # Stage1 manifest constraints are authoritative; the LLM may only
            # choose the semantic query text and top-k within that boundary.
            args[key] = value
    args.setdefault("top_k", 5)
    args.setdefault("sort_by_latest", True)
    return {**tool_call, "args": args}


def _tool_call_message(message: AIMessage, state: Stage2AgentState) -> AIMessage:
    calls = [_merge_stage1_filters(call, state) for call in message.tool_calls]
    return AIMessage(content=message.content, tool_calls=calls, id=message.id)


class Stage2Agent:
    """Stage2 LLM agent using the existing hybrid search ToolNode."""

    def __init__(self, model: Any, repository: Any) -> None:
        self.model = model
        self.repository = repository
        configure_repository(repository)
        self.graph = self._build_graph()

    def _system_prompt(self, state: Stage2AgentState) -> str:
        intent = state.get("intent", {})
        return f"""당신은 공시·재무 검색을 담당하는 Stage2 에이전트입니다.
사용자 원문 질문: {state.get('original_question', '')}
Stage1 Intent: {json.dumps(intent, ensure_ascii=False)}

반드시 dart_hybrid_search_tool을 먼저 호출해 근거 문서를 검색하세요.
Stage1이 제공한 기업·기간·공시 필터를 임의로 완화하거나 다른 기업으로 바꾸지 마세요.
검색 결과가 없으면 검색어를 짧게 바꿔 최대 두 번 재검색할 수 있습니다.
Stage2에서는 최종 답변을 만들지 말고, Stage3가 사용할 원문 근거를 확보하세요."""

    def _chatbot(self, state: Stage2AgentState) -> dict[str, Any]:
        bound = self.model.bind_tools(TOOLS)
        response = bound.invoke(
            [
                SystemMessage(content=self._system_prompt(state)),
                HumanMessage(content=state.get("search_query") or state.get("original_question", "")),
            ]
        )
        if not isinstance(response, AIMessage):
            response = AIMessage(content=str(getattr(response, "content", response)))
        return {
            "messages": [response],
            "trace": [f"stage2_chatbot_tool_calls={len(response.tool_calls)}"],
        }

    @staticmethod
    def _prepare_tool_call(state: Stage2AgentState) -> dict[str, Any]:
        messages = state.get("messages", [])
        if not messages or not isinstance(messages[-1], AIMessage) or not messages[-1].tool_calls:
            return {"trace": ["stage2_no_tool_call"]}
        return {
            "messages": [_tool_call_message(messages[-1], state)],
            "trace": ["stage2_stage1_filters_injected"],
        }

    @staticmethod
    def _collect_tool_result(state: Stage2AgentState) -> dict[str, Any]:
        message = _last_tool_message(state.get("messages", []))
        result = _payload(message.content if message is not None else {})
        if result:
            context = "\n\n".join(
                str(document.get("text", ""))
                for document in result.get("documents", [])
                if isinstance(document, dict)
            )
            return {
                "stage2_result": result,
                "context": context,
                "trace": [
                    f"stage2_documents={len(result.get('documents', []))}",
                    f"stage2_status={result.get('status', 'unknown')}",
                ],
            }
        return {
            "stage2_result": {
                "query_id": str(uuid.uuid4()),
                "documents": [],
                "cited_documents": [],
                "retrieval_trace": ["tool_result_unstructured"],
                "status": "not_found",
                "warnings": ["검색 Tool 결과가 구조화된 Stage2 bundle이 아닙니다."],
            },
            "context": str(message.content if message is not None else ""),
            "trace": ["stage2_tool_result_unstructured"],
        }

    def _query_transformer(self, state: Stage2AgentState) -> dict[str, Any]:
        response = self.model.invoke(
            [
                SystemMessage(content="검색 실패 시 공시 본문 검색에 사용할 짧은 핵심 키워드만 작성하세요."),
                HumanMessage(
                    content=(
                        f"원문 질문: {state.get('original_question', '')}\n"
                        f"이전 검색어: {state.get('search_query', '')}\n"
                        f"검색 결과: {state.get('context', '')[:1000]}"
                    )
                ),
            ]
        )
        rewritten = str(getattr(response, "content", response)).replace("\n", " ").strip()
        if not rewritten:
            rewritten = state.get("search_query") or state.get("original_question", "")
        return {
            "search_query": rewritten,
            "search_attempts": state.get("search_attempts", 0) + 1,
            "trace": [f"stage2_query_rewrite={rewritten[:80]}"],
        }

    @staticmethod
    def _finalize_no_tool(state: Stage2AgentState) -> dict[str, Any]:
        if state.get("stage2_result"):
            return {}
        return {
            "stage2_result": {
                "query_id": str(uuid.uuid4()),
                "documents": [],
                "cited_documents": [],
                "retrieval_trace": ["tool_not_called"],
                "status": "not_found",
                "warnings": ["Stage2 LLM이 검색 Tool을 호출하지 않았습니다."],
            },
            "trace": ["stage2_finalize_without_tool"],
        }

    @staticmethod
    def _after_collect(state: Stage2AgentState) -> str:
        result = state.get("stage2_result", {})
        if result.get("documents"):
            return "finalize"
        if state.get("search_attempts", 0) < 2:
            return "rewrite"
        return "finalize"

    def _build_graph(self):
        builder = StateGraph(Stage2AgentState)
        builder.add_node("chatbot", self._chatbot)
        builder.add_node("prepare_tool_call", self._prepare_tool_call)
        builder.add_node("tools", ToolNode(TOOLS))
        builder.add_node("collect_tool_result", self._collect_tool_result)
        builder.add_node("query_transformer", self._query_transformer)
        builder.add_node("finalize", self._finalize_no_tool)
        builder.add_edge(START, "chatbot")
        builder.add_edge("chatbot", "prepare_tool_call")
        builder.add_conditional_edges(
            "prepare_tool_call",
            tools_condition,
            {"tools": "tools", END: "finalize"},
        )
        builder.add_edge("tools", "collect_tool_result")
        builder.add_conditional_edges(
            "collect_tool_result",
            self._after_collect,
            {"rewrite": "query_transformer", "finalize": "finalize"},
        )
        builder.add_edge("query_transformer", "chatbot")
        builder.add_edge("finalize", END)
        return builder.compile()

    def run(self, *, question: str, intent: dict[str, Any]) -> dict[str, Any]:
        state = self.graph.invoke(
            {
                "messages": [HumanMessage(content=question)],
                "original_question": question,
                "intent": intent,
                "search_query": question,
                "search_attempts": 0,
                "trace": ["stage2_start"],
                "warnings": [],
            }
        )
        result = dict(state.get("stage2_result") or {})
        result["original_question"] = question
        result["search"] = {
            **dict(result.get("search") or {}),
            "query": state.get("search_query", question),
            "attempt": state.get("search_attempts", 0) + 1,
        }
        result["retrieval_trace"] = [
            *list(result.get("retrieval_trace") or []),
            *list(state.get("trace") or []),
        ]
        result["warnings"] = [*list(result.get("warnings") or []), *list(state.get("warnings") or [])]
        return result


__all__ = ["Stage2Agent", "Stage2AgentState"]
