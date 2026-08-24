import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from rag.clients.rag_reasoning import RagReasoningClient
from rag.retrieval.rerank import RerankedResult
from rag.schemas import RetrievedDocument
from rag.generation.tool_schema import SEARCH_TOOL


@dataclass
class GeneratedAnswer:
    answer: str
    documents: list[RetrievedDocument] = field(default_factory=list)
    think_trace: str = ""


class RagAnswerGenerator:
    def __init__(self, client: RagReasoningClient | None = None, max_rounds: int = 2) -> None:
        self.client = client or RagReasoningClient()
        self.max_rounds = max(1, max_rounds)

    def generate(
        self,
        question: str,
        search: Callable[[str], RerankedResult],
        initial_documents: list[RetrievedDocument] | None = None,
    ) -> GeneratedAnswer:
        initial_documents = initial_documents or []
        initial_context = "\n\n".join(
            f"[출처: {document.source}]\n{document.text}"
            for document in initial_documents
        )
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"질문:\n{question}\n\n"
                    f"초기 검색·리랭킹 공시 문서:\n{initial_context}\n\n"
                    "아래 공시 문서만 근거로 답변하세요. 근거가 없는 내용은 추측하지 마세요."
                ),
            }
        ]
        all_documents: dict[str, RetrievedDocument] = {
            document.id: document for document in initial_documents
        }
        traces: list[str] = []

        for _ in range(self.max_rounds):
            response = self.client.generate(messages, [SEARCH_TOOL])
            message = response.get("message", {}) or {}
            thinking = str(message.get("thinkingContent", "")).strip()
            if thinking:
                traces.append(thinking)
            tool_calls = message.get("toolCalls", []) or []
            if not tool_calls:
                return GeneratedAnswer(
                    answer=str(message.get("content", "")).strip(),
                    documents=list(all_documents.values()),
                    think_trace=" ".join(traces),
                )

            messages.append(message)
            for tool_call in tool_calls:
                function = tool_call.get("function", {}) or {}
                query = self._query_from_arguments(function.get("arguments", {}))
                if not query:
                    continue
                reranked = search(query)
                for document in reranked.documents:
                    all_documents[document.id] = document
                tool_payload = {
                    "query": query,
                    "answer": reranked.answer,
                    "suggestedQueries": reranked.suggested_queries,
                    "documents": [
                        {
                            "id": document.id,
                            "source": document.source,
                            "text": document.text,
                            "metadata": document.metadata,
                        }
                        for document in reranked.documents
                    ],
                }
                messages.append({
                    "role": "tool",
                    "toolCallId": str(tool_call.get("id", "")),
                    "content": json.dumps(tool_payload, ensure_ascii=False),
                })

        return GeneratedAnswer(
            answer="관련 공시 근거를 바탕으로 답변을 완성하지 못했습니다.",
            documents=list(all_documents.values()),
            think_trace=" ".join(traces),
        )

    @staticmethod
    def _query_from_arguments(arguments: Any) -> str:
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                return arguments.strip()
        if isinstance(arguments, dict):
            return str(arguments.get("query", "")).strip()
        return ""
