from rag.clients.base import ClovaApiClient


class RagReasoningClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def generate(self, messages: list[dict], tools: list[dict], *, tool_choice: str = "auto", top_p: float = 0.8, top_k: int = 0, max_tokens: int = 1024, temperature: float = 0.2, repetition_penalty: float = 1.1, seed: int = 0, include_ai_filters: bool = True) -> dict:
        result = self.api.result_or_raise(self.api.post("/v1/api-tools/rag-reasoning", {
            "messages": messages, "tools": tools, "toolChoice": tool_choice,
            "topP": top_p, "topK": top_k, "maxTokens": max_tokens,
            "temperature": temperature, "repetitionPenalty": repetition_penalty,
            "stop": [], "seed": seed, "includeAiFilters": include_ai_filters,
        }), "RAG Reasoning")
        return result
