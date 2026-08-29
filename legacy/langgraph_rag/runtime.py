"""Runtime components owned by the LangGraph RAG backend.

This module intentionally has no dependency on the classic rag package.
"""

from __future__ import annotations

import json
import math
import re
import hashlib
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from common.schemas import RetrievedDocument


class ClovaApiClient:
    def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        from common.config import settings

        if not settings.clova_api_key:
            raise RuntimeError("CLOVA_API_KEY is not configured.")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {settings.clova_api_key}",
        }
        if settings.clova_request_id:
            headers["X-NCP-CLOVASTUDIO-REQUEST-ID"] = settings.clova_request_id
        request = Request(
            f"https://{settings.clova_api_host}{path}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=120) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"CLOVA API request failed: HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"CLOVA API request failed: {error}") from error

    @staticmethod
    def result_or_raise(response: dict[str, Any], operation: str) -> dict[str, Any]:
        status = response.get("status", {})
        if status.get("code") != "20000":
            raise RuntimeError(f"CLOVA {operation} failed: {status}")
        return response.get("result", {})


@dataclass(frozen=True)
class EmbeddingResult:
    vector: list[float]
    input_tokens: int


class EmbeddingClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def embed_text(self, text: str) -> EmbeddingResult:
        if not text.strip():
            return EmbeddingResult([], 0)
        result = self.api.result_or_raise(
            self.api.post("/v1/api-tools/embedding/v2", {"text": text}), "embedding"
        )
        vector = result.get("embedding", [])
        if len(vector) != 1024:
            raise RuntimeError(f"Unexpected embedding dimension: {len(vector)}")
        return EmbeddingResult(vector, result.get("inputTokens", 0))


class FakeEmbeddingClient:
    """Deterministic local embedding for no-cost smoke tests."""

    def embed_text(self, text: str) -> EmbeddingResult:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        vector = [((digest[index % len(digest)] / 255.0) * 2.0) - 1.0 for index in range(32)]
        return EmbeddingResult(vector, 0)


@dataclass(frozen=True)
class SegmentationResult:
    paragraphs: list[str]
    spans: list[list[int]]
    input_tokens: int


class SegmentationClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def segment_text(
        self,
        text: str,
        *,
        alpha: float = -100,
        seg_cnt: int = -1,
        post_process: bool = True,
        post_process_max_size: int = 1000,
        post_process_min_size: int = 300,
        max_input_chars: int = 30_000,
    ) -> SegmentationResult:
        if not text.strip():
            return SegmentationResult([], [], 0)
        if len(text) > max_input_chars:
            paragraphs: list[str] = []
            input_tokens = 0
            for start in range(0, len(text), max_input_chars):
                result = self._segment_single(
                    text[start : start + max_input_chars],
                    alpha=alpha,
                    seg_cnt=seg_cnt,
                    post_process=post_process,
                    post_process_max_size=post_process_max_size,
                    post_process_min_size=post_process_min_size,
                )
                paragraphs.extend(result.paragraphs)
                input_tokens += result.input_tokens
            return SegmentationResult(paragraphs, [], input_tokens)
        return self._segment_single(
            text,
            alpha=alpha,
            seg_cnt=seg_cnt,
            post_process=post_process,
            post_process_max_size=post_process_max_size,
            post_process_min_size=post_process_min_size,
        )

    def _segment_single(self, text: str, **options: Any) -> SegmentationResult:
        result = self.api.result_or_raise(
            self.api.post(
                "/v1/api-tools/segmentation",
                {
                    "text": text,
                    "alpha": options["alpha"],
                    "segCnt": options["seg_cnt"],
                    "postProcess": options["post_process"],
                    "postProcessMaxSize": options["post_process_max_size"],
                    "postProcessMinSize": options["post_process_min_size"],
                },
            ),
            "segmentation",
        )
        paragraphs: list[str] = []
        spans: list[list[int]] = []
        for item in result.get("topicSeg", []) or []:
            if isinstance(item, dict):
                value = str(item.get("text", item.get("content", ""))).strip()
                if value:
                    paragraphs.append(value)
                span = item.get("span") or item.get("range") or []
                spans.append(list(span) if isinstance(span, list) else [])
            elif str(item).strip():
                paragraphs.append(str(item).strip())
        return SegmentationResult(paragraphs, spans, result.get("inputTokens", 0))


@dataclass(frozen=True)
class RerankerResult:
    result: str
    cited_documents: list[dict[str, Any]]
    suggested_queries: list[str]
    usage: dict[str, Any]


class RerankerClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def rerank_documents(self, query: str, documents: list[dict[str, Any]], *, max_tokens: int = 1024) -> RerankerResult:
        if not documents:
            return RerankerResult("", [], [], {})
        payload = {
            "documents": [{"id": str(item["id"]), "doc": str(item["doc"])} for item in documents],
            "query": query,
            "maxTokens": max_tokens,
        }
        result = self.api.result_or_raise(
            self.api.post("/v1/api-tools/reranker", payload), "reranker"
        )
        suggested = result.get("suggestedQueries", []) or []
        if isinstance(suggested, str):
            suggested = [suggested]
        return RerankerResult(
            result.get("result", ""),
            result.get("citedDocuments", []),
            suggested,
            result.get("usage", {}),
        )


class RagReasoningClient:
    def __init__(self, api: ClovaApiClient | None = None) -> None:
        self.api = api or ClovaApiClient()

    def generate(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        payload = {
            "messages": messages,
            "tools": tools,
            "toolChoice": "auto",
            "topP": 0.8,
            "topK": 0,
            "maxTokens": 1024,
            "temperature": 0.2,
            "repetitionPenalty": 1.1,
            "stop": [],
            "seed": 0,
            "includeAiFilters": True,
        }
        return self.api.result_or_raise(
            self.api.post("/v1/api-tools/rag-reasoning", payload), "RAG Reasoning"
        )


@dataclass(frozen=True)
class LocalVectorRow:
    id: str
    text: str
    source_path: str
    embedding: list[float]
    metadata: dict[str, Any]


class LocalVectorStore:
    def __init__(self) -> None:
        self.rows: dict[str, LocalVectorRow] = {}

    @classmethod
    def load(cls, path: str) -> "LocalVectorStore":
        store = cls()
        target = Path(path)
        if target.exists():
            store.upsert(LocalVectorRow(**item) for item in json.loads(target.read_text(encoding="utf-8")))
        return store

    def save(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "id": row.id,
                "text": row.text,
                "source_path": row.source_path,
                "embedding": row.embedding,
                "metadata": row.metadata,
            }
            for row in self.rows.values()
        ]
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def upsert(self, rows: Iterable[LocalVectorRow]) -> int:
        materialized = list(rows)
        for row in materialized:
            if not row.embedding:
                raise ValueError(f"Empty embedding: {row.id}")
            self.rows[row.id] = row
        return len(materialized)

    def search(self, embedding: list[float], *, limit: int = 20, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        scored = []
        for row in self.rows.values():
            if filters and any(
                value and (
                    value not in str(row.metadata.get(key, ""))
                    if key == "document_type"
                    else str(row.metadata.get(key, "")) != value
                )
                for key, value in filters.items()
            ):
                continue
            score = self._cosine(embedding, row.embedding)
            scored.append({"id": row.id, "text": row.text, "source_path": row.source_path, "score": score, **row.metadata})
        return sorted(scored, key=lambda item: item["score"], reverse=True)[:limit]

    def list_corp_names(self) -> list[str]:
        return sorted({str(row.metadata.get("corp_name", "")) for row in self.rows.values() if row.metadata.get("corp_name")})

    def __len__(self) -> int:
        return len(self.rows)

    @staticmethod
    def _cosine(left: list[float], right: list[float]) -> float:
        if len(left) != len(right):
            raise ValueError("Embedding dimensions do not match")
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return 0.0
        return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class PostgresStore:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def initialize(self) -> None:
        schema = """
        CREATE EXTENSION IF NOT EXISTS vector;
        CREATE TABLE IF NOT EXISTS documents (
            id TEXT PRIMARY KEY, corp_name TEXT NOT NULL DEFAULT '', corp_code TEXT NOT NULL DEFAULT '',
            document_type TEXT NOT NULL DEFAULT '', market TEXT NOT NULL DEFAULT '', report_period TEXT NOT NULL DEFAULT '',
            disclosure_date DATE, source_path TEXT NOT NULL, source_hash TEXT NOT NULL,
            file_extension TEXT NOT NULL DEFAULT '', source_group TEXT NOT NULL DEFAULT '', created_at TIMESTAMPTZ NOT NULL DEFAULT now(), updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS document_chunks (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL, text TEXT NOT NULL, embedding vector(1024), span_json JSONB NOT NULL DEFAULT '[]'::jsonb,
            source_path TEXT NOT NULL, text_hash TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE (document_id, chunk_index)
        );
        CREATE INDEX IF NOT EXISTS document_chunks_embedding_idx ON document_chunks USING hnsw (embedding vector_cosine_ops);
        """
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(schema)
            connection.commit()

    def document_is_current(self, document_id: str, source_hash: str) -> bool:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT d.source_hash, COUNT(c.id) FROM documents d LEFT JOIN document_chunks c ON c.document_id = d.id WHERE d.id = %s GROUP BY d.source_hash",
                    (document_id,),
                )
                row = cursor.fetchone()
        return bool(row and row[0] == source_hash and row[1] > 0)

    def upsert_document(self, document: Any) -> None:
        query = """
        INSERT INTO documents (id, corp_name, corp_code, document_type, market, report_period, disclosure_date, source_path, source_hash, file_extension, source_group)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET corp_name=EXCLUDED.corp_name, corp_code=EXCLUDED.corp_code,
        document_type=EXCLUDED.document_type, market=EXCLUDED.market, report_period=EXCLUDED.report_period,
        disclosure_date=EXCLUDED.disclosure_date, source_path=EXCLUDED.source_path, source_hash=EXCLUDED.source_hash,
        file_extension=EXCLUDED.file_extension, source_group=EXCLUDED.source_group, updated_at=now()
        """
        values = (document.document_id, document.corp_name, document.corp_code, document.document_type, document.market,
                  document.report_period, document.disclosure_date, str(document.source_path), document.source_hash,
                  document.file_extension, document.source_group)
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, values)
            connection.commit()

    def delete_chunks(self, document_id: str) -> None:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM document_chunks WHERE document_id = %s", (document_id,))
            connection.commit()

    def upsert_chunks(self, chunks: Iterable[Any]) -> int:
        rows = list(chunks)
        if not rows:
            return 0
        query = """
        INSERT INTO document_chunks (id, document_id, chunk_index, text, embedding, span_json, source_path, text_hash)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET text=EXCLUDED.text, embedding=EXCLUDED.embedding,
        span_json=EXCLUDED.span_json, source_path=EXCLUDED.source_path, text_hash=EXCLUDED.text_hash
        """
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.executemany(query, [
                    (chunk.chunk_id, chunk.document_id, chunk.chunk_index, chunk.text, chunk.embedding,
                     json.dumps(chunk.span), chunk.source_path, chunk.text_hash)
                    for chunk in rows
                ])
            connection.commit()
        return len(rows)

    @contextmanager
    def connection(self):
        try:
            import psycopg
            from pgvector.psycopg import register_vector
        except ImportError as error:
            raise RuntimeError("Install psycopg[binary] and pgvector before using PostgreSQL.") from error
        with psycopg.connect(self.dsn) as connection:
            register_vector(connection)
            yield connection

    def search(self, embedding: list[float], *, limit: int = 20, filters: dict[str, str] | None = None) -> list[dict[str, Any]]:
        conditions = ["c.embedding IS NOT NULL"]
        parameters: list[Any] = [embedding]
        for field in ("corp_name", "corp_code", "document_type", "report_period", "source_group"):
            value = (filters or {}).get(field)
            if value:
                conditions.append(f"d.{field} ILIKE %s" if field == "document_type" else f"d.{field} = %s")
                parameters.append(f"%{value}%" if field == "document_type" else value)
        parameters.extend([embedding, limit])
        query = f"""
        SELECT c.id, c.text, c.source_path, d.corp_name, d.corp_code,
               d.document_type, d.report_period, d.disclosure_date,
               d.source_group, 1 - (c.embedding <=> %s) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE {' AND '.join(conditions)}
        ORDER BY c.embedding <=> %s
        LIMIT %s
        """
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(query, parameters)
                columns = [description.name for description in cursor.description]
                return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def list_corp_names(self) -> list[str]:
        with self.connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT DISTINCT corp_name FROM documents WHERE corp_name <> '' ORDER BY corp_name")
                return [str(row[0]) for row in cursor.fetchall()]


_PERIOD_BY_QUARTER = {"1": "03", "2": "06", "3": "09", "4": "12"}


def extract_metadata_filters(query: str, *, corp_names: Iterable[str] = ()) -> dict[str, str]:
    filters: dict[str, str] = {}
    normalized = " ".join(query.split())
    for corp_name in sorted({name.strip() for name in corp_names if name and name.strip()}, key=len, reverse=True):
        if corp_name in normalized:
            filters["corp_name"] = corp_name
            break
    year_match = re.search(r"(20\d{2})\s*년", normalized)
    quarter_match = re.search(r"(20\d{2})\s*년\s*([1-4])\s*분기", normalized)
    if quarter_match:
        year, quarter = quarter_match.groups()
        filters["report_period"] = f"{year}-{_PERIOD_BY_QUARTER[quarter]}"
    elif year_match and any(keyword in normalized for keyword in ("사업보고서", "연간")):
        filters["report_period"] = f"{year_match.group(1)}-12"
    elif year_match and any(keyword in normalized for keyword in ("반기", "상반기")):
        filters["report_period"] = f"{year_match.group(1)}-06"
    for document_type in ("사업보고서", "반기보고서", "분기보고서"):
        if document_type in normalized:
            filters["document_type"] = document_type
            break
    return filters


@dataclass
class VectorRetriever:
    store: LocalVectorStore | PostgresStore
    embedder: EmbeddingClient
    default_limit: int = 20

    def list_corp_names(self) -> list[str]:
        return self.store.list_corp_names()

    def search(self, query: str, *, limit: int | None = None, filters: dict[str, str] | None = None) -> list[RetrievedDocument]:
        if not query.strip():
            return []
        filters = filters or extract_metadata_filters(query, corp_names=self.list_corp_names())
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
                    for key in ("corp_name", "corp_code", "document_type", "report_period", "disclosure_date", "source_group")
                    if row.get(key) is not None
                },
            )
            for row in rows
        ]


@dataclass
class RerankedResult:
    answer: str
    documents: list[RetrievedDocument]
    suggested_queries: list[str]


class DocumentReranker:
    def __init__(self, client: RerankerClient | None = None) -> None:
        self.client = client or RerankerClient()

    def rerank(self, query: str, documents: list[RetrievedDocument], *, max_tokens: int = 1024) -> RerankedResult:
        if not documents:
            return RerankedResult("", [], [])
        response = self.client.rerank_documents(
            query, [{"id": document.id, "doc": document.text} for document in documents], max_tokens=max_tokens
        )
        by_id = {document.id: document for document in documents}
        cited = [by_id[str(item.get("id", ""))] for item in response.cited_documents if str(item.get("id", "")) in by_id]
        return RerankedResult(response.result, cited, response.suggested_queries)


@dataclass(frozen=True)
class AlternativeDocuments:
    same_company: list[RetrievedDocument]
    same_period: list[RetrievedDocument]

    def as_dict(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "same_company": [document.model_dump() for document in self.same_company],
            "same_period": [document.model_dump() for document in self.same_period],
        }


class AlternativeFinder:
    def __init__(self, retriever: VectorRetriever, *, limit: int = 3) -> None:
        self.retriever = retriever
        self.limit = limit

    def find(self, question: str) -> AlternativeDocuments:
        filters = extract_metadata_filters(question, corp_names=self.retriever.list_corp_names())
        company, period = filters.get("corp_name"), filters.get("report_period")
        same_company = self._filtered(question, {"corp_name": company} if company else {}, period, "report_period")
        same_period = self._filtered(question, {"report_period": period} if period else {}, company, "corp_name")
        return AlternativeDocuments(same_company, same_period)

    def _filtered(self, question: str, filters: dict[str, str], excluded: str | None, key: str) -> list[RetrievedDocument]:
        if not filters:
            return []
        documents = self.retriever.search(question, limit=self.limit + 1, filters=filters)
        unique: list[RetrievedDocument] = []
        seen: set[tuple[str, str, str]] = set()
        for document in documents:
            if excluded and document.metadata.get(key) == excluded:
                continue
            marker = (document.source, str(document.metadata.get("corp_name", "")), str(document.metadata.get("report_period", "")))
            if marker not in seen:
                seen.add(marker)
                unique.append(document)
            if len(unique) >= self.limit:
                break
        return unique
