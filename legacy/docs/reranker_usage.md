# Reranker usage contract

This document records the reranker behavior shared by the original `rag/` implementation and the independent `langgraph_rag/` implementation.

## Intended contract

1. Retrieval produces a broad candidate set up to `retrieval_top_k`.
2. Reranking receives only the first `rerank_top_k` candidates.
3. Only the reranker-selected `cited_documents` are sent to answer generation.
4. The same retrieval and reranking limits apply after a query rewrite or a RAG Reasoning search round.
5. If reranking returns no documents, the flow falls back without generating an uncited answer.

## Findings and corrections

### `langgraph_rag/`

The node called the reranker with every retrieved document, so the configured rerank limit was not enforced. Also, `generate_answer` did not include `cited_documents` in its prompt; the evaluator saw the documents, but the answer model did not. Both issues are fixed. The graph now passes `settings.rerank_top_k` into the node and injects the selected documents into each answer attempt, including after query rewriting.

### `rag/`

The initial retrieval and reranking limits were already applied correctly. The problem was that the initial reranked documents were used only as an empty-result gate/fallback. They were not included in the first answer-generation prompt. The generator now accepts `initial_documents`, seeds its cited-document collection with them, and includes them in the prompt. The existing tool loop remains available for follow-up searches, and each tool search still applies retrieval and reranking limits.

## Why this matters

Reranking is not useful merely because it runs. Its output must be the context boundary for generation. Without that handoff, the model can answer from latent knowledge or from an earlier prompt, while groundedness evaluation checks a different document set.
