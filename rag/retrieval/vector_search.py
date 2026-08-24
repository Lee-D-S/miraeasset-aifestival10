from dataclasses import dataclass

from rag.clients.embedding import EmbeddingClient
from rag.retrieval.filters import extract_metadata_filters
from common.schemas import RetrievedDocument
from rag.storage.postgres import PostgresStore


@dataclass
class VectorRetriever:
    store: PostgresStore
    embedder: EmbeddingClient
    default_limit: int = 20

    def list_corp_names(self) -> list[str]:
        if hasattr(self.store, "list_corp_names"):
            return self.store.list_corp_names()
        return []

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[RetrievedDocument]:
        if not query.strip():
            return []
        if filters is None:
            corp_names = []
            rows = getattr(self.store, "rows", {})
            for row in rows.values():
                corp_names.append(str(row.metadata.get("corp_name", "")))
            filters = extract_metadata_filters(query, corp_names=corp_names)
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
