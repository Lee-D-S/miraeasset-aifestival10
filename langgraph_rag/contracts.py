from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class RetrieverPort(Protocol):
    def search(self, query: str, *, limit: int) -> list[dict[str, Any]]: ...


class RerankerPort(Protocol):
    def rerank(self, query: str, documents: Sequence[dict[str, Any]]) -> dict[str, Any]: ...


class LlmPort(Protocol):
    def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class GraphDependencies:
    retriever: RetrieverPort
    reranker: RerankerPort
    llm: LlmPort

