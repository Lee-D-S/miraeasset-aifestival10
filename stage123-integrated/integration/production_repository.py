from __future__ import annotations

import uuid
from typing import Any

from .stage2_repository import RetrievalError, SearchRequest, Stage2SearchResult


class ProductionStage2Repository:
    """SQLite + Chroma adapter for the original Stage2 database contract."""

    def __init__(self, engine: Any, vectorstore: Any, *, fetch_ids: Any, search_vectors: Any) -> None:
        self.engine = engine
        self.vectorstore = vectorstore
        self.fetch_ids = fetch_ids
        self.search_vectors = search_vectors

    @classmethod
    def from_environment(cls) -> "ProductionStage2Repository":
        try:
            from app.agent.db import rdb_engine, vectorstore
            from app.tools.rdb_methods import fetch_filtered_chunk_ids
            from app.tools.vectordb_methods import search_vector_by_ids
        except Exception as error:  # noqa: BLE001 - dependency boundary
            raise RetrievalError(
                "dependency_issue",
                f"production SQLite+Chroma adapter is unavailable: {type(error).__name__}",
            ) from error
        return cls(
            rdb_engine,
            vectorstore,
            fetch_ids=fetch_filtered_chunk_ids,
            search_vectors=search_vector_by_ids,
        )

    def search(self, request: SearchRequest) -> Stage2SearchResult:
        try:
            ids = self.fetch_ids(
                engine=self.engine,
                corp_names=list(request.corp_names),
                start_date=request.start_date,
                end_date=request.end_date,
                section_name=request.section_name,
                sector=request.sector,
                exclude_corp_name=request.exclude_corp_name,
                doc_group=request.doc_group,
                doc_group_candidates=list(request.doc_group_candidates),
                doc_subtype=request.doc_subtype,
                doc_subtype_candidates=list(request.doc_subtype_candidates),
                base_years=list(request.base_years),
                base_months=list(request.base_months),
                is_correction=request.is_correction,
                report_nm_contains=list(request.report_nm_contains),
                sort_by_latest=request.sort_by_latest,
                limit=request.limit,
            )
            docs = self.search_vectors(
                vectorstore=self.vectorstore,
                query=request.query,
                chunk_ids=ids,
                top_k=request.top_k,
            )
        except Exception as error:  # noqa: BLE001 - DB/vector boundary
            raise RetrievalError("stage2_existing_bug", f"production retrieval failed: {type(error).__name__}") from error

        documents: list[dict[str, Any]] = []
        for document in docs or []:
            metadata = dict(getattr(document, "metadata", {}) or {})
            identifier = str(metadata.get("chunk_id") or getattr(document, "id", ""))
            if not identifier:
                raise RetrievalError("schema_issue", "production Stage2 document has no chunk_id")
            documents.append(
                {
                    "id": identifier,
                    "source": str(metadata.get("file_path", "")),
                    "text": str(getattr(document, "page_content", "") or ""),
                    "score": metadata.get("score"),
                    "metadata": metadata,
                    "evidence_spans": [],
                }
            )
        return Stage2SearchResult(
            query_id=str(uuid.uuid4()),
            search=request,
            documents=documents,
            retrieval_trace=["backend=sqlite_chroma", f"candidate_ids={len(ids)}", f"documents={len(documents)}"],
            status="ok" if documents else "not_found",
            warnings=[] if documents else ["SQLite/Chroma 검색 결과가 없습니다."],
        )
