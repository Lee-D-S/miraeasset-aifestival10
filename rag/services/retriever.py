from dataclasses import dataclass

from common.schemas import RetrievedDocument


@dataclass
class Retriever:
    """Temporary local retriever; replace with vector DB retrieval later."""

    documents: list[RetrievedDocument]

    def search(self, query: str, limit: int = 5) -> list[RetrievedDocument]:
        query_terms = {term.lower() for term in query.split() if term.strip()}
        ranked = []
        for document in self.documents:
            document_terms = set(document.text.lower().split())
            overlap = len(query_terms & document_terms)
            if overlap:
                ranked.append(document.model_copy(update={"score": min(overlap / max(len(query_terms), 1), 1)}))
        return sorted(ranked, key=lambda item: item.score, reverse=True)[:limit]
