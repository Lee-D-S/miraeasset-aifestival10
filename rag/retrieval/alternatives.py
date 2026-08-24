from dataclasses import dataclass

from common.schemas import RetrievedDocument
from rag.retrieval.filters import extract_metadata_filters
from rag.retrieval.vector_search import VectorRetriever


@dataclass(frozen=True)
class AlternativeDocuments:
    same_company: list[RetrievedDocument]
    same_period: list[RetrievedDocument]

    def as_dict(self) -> dict[str, list[dict]]:
        return {
            "same_company": [document.model_dump() for document in self.same_company],
            "same_period": [document.model_dump() for document in self.same_period],
        }


class AlternativeFinder:
    def __init__(self, retriever: VectorRetriever, *, limit: int = 3) -> None:
        self.retriever = retriever
        self.limit = limit

    def find(self, question: str) -> AlternativeDocuments:
        corp_names = self.retriever.list_corp_names()
        filters = extract_metadata_filters(question, corp_names=corp_names)
        company = filters.get("corp_name")
        period = filters.get("report_period")

        same_company = []
        if company:
            same_company = self.retriever.search(
                question,
                limit=self.limit + 1,
                filters={"corp_name": company},
            )
            same_company = [
                document for document in same_company
                if not period or document.metadata.get("report_period") != period
            ]
            same_company = self._deduplicate(same_company)

        same_period = []
        if period:
            same_period = self.retriever.search(
                question,
                limit=self.limit + 1,
                filters={"report_period": period},
            )
            same_period = [
                document for document in same_period
                if not company or document.metadata.get("corp_name") != company
            ]
            same_period = self._deduplicate(same_period)

        return AlternativeDocuments(same_company=same_company, same_period=same_period)

    def _deduplicate(self, documents: list[RetrievedDocument]) -> list[RetrievedDocument]:
        unique: list[RetrievedDocument] = []
        seen: set[tuple[str, str, str]] = set()
        for document in documents:
            key = (
                document.source,
                str(document.metadata.get("corp_name", "")),
                str(document.metadata.get("report_period", "")),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(document)
            if len(unique) >= self.limit:
                break
        return unique
