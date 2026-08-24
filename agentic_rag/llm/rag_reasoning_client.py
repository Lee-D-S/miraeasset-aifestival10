from __future__ import annotations

import json
from typing import Any, Callable
from urllib.request import Request, urlopen

from agentic_rag.infrastructure.retry import retry_call


LOCAL_RETRIEVAL_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "local_corpus_search",
        "description": "대회에서 승인된 local corpus에서 질문과 관련된 문서를 검색합니다.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "검색 질의"}},
            "required": ["query"],
        },
    },
}


def _content_text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
    return str(content or "")


class RagReasoningClient:
    """RAG Reasoning의 승인 검색 함수와 tool-message 왕복을 담당한다."""

    ENDPOINT = "/v1/api-tools/rag-reasoning"

    def __init__(
        self,
        *,
        transport: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        api_host: str | None = None,
        api_key: str | None = None,
        search: Callable[[str], list[dict[str, Any]]] | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self.transport = transport
        self.api_host = api_host
        self.api_key = api_key
        self.search = search
        self.max_tokens = max(1024, min(max_tokens, 4096))
        self.calls = 0

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if self.transport is not None:
            return self.transport(payload)
        if not self.api_host or not self.api_key:
            raise RuntimeError("RAG Reasoning API credentials are not configured")
        request = Request(
            f"https://{self.api_host}{self.ENDPOINT}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {self.api_key}"},
            method="POST",
        )
        with retry_call(lambda: urlopen(request, timeout=120)) as response:
            body = json.loads(response.read().decode("utf-8"))
        if body.get("status", {}).get("code") != "20000":
            raise RuntimeError(f"RAG Reasoning request failed: {body.get('status')}")
        return body.get("result", {})

    @staticmethod
    def _message(result: dict[str, Any]) -> dict[str, Any]:
        message = result.get("message", {})
        if message:
            return message
        messages = result.get("messages", [])
        return messages[-1] if messages else {}

    def generate_grounded_answer(self, question: str, documents: list[dict[str, Any]]) -> dict[str, Any]:
        if not documents:
            raise ValueError("RAG Reasoning requires at least one evidence document")
        if self.search is None:
            raise RuntimeError("Approved local corpus search function is not configured")

        messages = [{"role": "user", "content": question}]
        first = self._request({"messages": messages, "tools": [LOCAL_RETRIEVAL_TOOL], "toolChoice": "auto", "maxTokens": self.max_tokens})
        assistant = self._message(first)
        tool_calls = assistant.get("toolCalls", [])
        if not tool_calls:
            raise ValueError("RAG Reasoning did not select an approved retrieval function")

        tool_messages: list[dict[str, Any]] = []
        executed: list[dict[str, Any]] = []
        for call in tool_calls:
            function = call.get("function", {})
            if function.get("name") != LOCAL_RETRIEVAL_TOOL["function"]["name"]:
                raise ValueError("Unapproved RAG function requested")
            arguments = function.get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            query = str(arguments.get("query", "")).strip()
            if not query:
                raise ValueError("RAG retrieval function requires a query")
            result_documents = self.search(query)
            result_documents = [
                {"id": str(item.get("id", "")), "doc": str(item.get("text", item.get("doc", "")))}
                for item in result_documents
                if item.get("id") and (item.get("text") or item.get("doc"))
            ]
            executed.extend(result_documents)
            tool_messages.append({
                "role": "tool",
                "toolCallId": call.get("id", ""),
                "content": json.dumps({"search_result": result_documents}, ensure_ascii=False),
            })

        final_messages = messages + [{"role": "assistant", "content": _content_text(assistant.get("content")), "toolCalls": tool_calls}] + tool_messages
        final = self._request({"messages": final_messages, "tools": [LOCAL_RETRIEVAL_TOOL], "toolChoice": "none", "maxTokens": self.max_tokens})
        final_message = self._message(final)
        answer = _content_text(final_message.get("content"))
        if not answer.strip():
            raise ValueError("RAG Reasoning returned an empty answer")
        return {"answer": answer, "tool_calls": tool_calls, "documents": executed, "usage": final.get("usage", {})}
