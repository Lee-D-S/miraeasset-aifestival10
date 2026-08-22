from dataclasses import dataclass

from rag.clients.embedding import EmbeddingClient
from rag.schemas import RetrievedDocument
from rag.storage.postgres import PostgresStore


@dataclass
class VectorRetriever:
    store: PostgresStore
    embedder: EmbeddingClient
    default_limit: int = 20

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedDocument]:
        if not query.strip():
            return []
        vector = self.embedder.embed_text(query).vector
        rows = self.store.search(vector, limit=limit or self.default_limit, filters=filters)
        return [
            RetrievedDocument(
                id=str(row["id"]),
                source=str(row["source_path"]),
                text=str(row["text"]),
                score=max(0.0, min(1.0, float(row.get("score", 0.0)))),
                metadata={
                    key: row.get(key)
                    for key in (
                        "corp_name",
                        "corp_code",
                        "document_type",
                        "report_period",
                        "disclosure_date",
                        "source_group",
                    )
                    if row.get(key) is not None
                },
            )
            for row in rows
        ]
