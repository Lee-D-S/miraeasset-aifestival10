from __future__ import annotations

import json
import math
import re
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable

from .stage2_repository import RetrievalError, SearchRequest, Stage2SearchResult


_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _period_parts(value: Any) -> tuple[int | None, int | None]:
    match = re.match(r"^(20\d{2})-(\d{1,2})$", _text(value))
    if not match:
        return None, None
    return int(match.group(1)), int(match.group(2))


def _doc_subtype(metadata: dict[str, Any]) -> str | None:
    report = _text(metadata.get("document_type"))
    if "사업보고서" in report:
        return "annual"
    if "반기보고서" in report:
        return "half"
    if "분기보고서" in report:
        return "quarter"
    _year, month = _period_parts(metadata.get("report_period"))
    return {3: "quarter", 6: "half", 9: "quarter", 12: "annual"}.get(month)


def _cosine(left: list[float], right: list[float]) -> float:
    if len(left) != len(right):
        raise RetrievalError(
            "schema_issue",
            f"embedding dimension mismatch: stored={len(left)}, query={len(right)}",
        )
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        raise RetrievalError("schema_issue", "zero-length embedding cannot be ranked")
    return sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)


class JsonStage2Repository:
    """Stage2 repository backed by the local disclosure JSON fixture.

    The normal ``search`` path requires a query embedding.  Keyword ranking is
    intentionally exposed separately for deterministic smoke tests and is not
    used by the E2E runner, so a missing API key cannot look like a successful
    semantic retrieval.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        *,
        source_path: Path,
        query_embedder: Callable[[str], Iterable[float]] | None = None,
    ) -> None:
        self.rows = rows
        self.source_path = source_path
        self.query_embedder = query_embedder

    @classmethod
    def from_path(
        cls,
        path: str | Path,
        *,
        query_embedder: Callable[[str], Iterable[float]] | None = None,
    ) -> "JsonStage2Repository":
        source_path = Path(path).expanduser().resolve()
        if not source_path.exists():
            raise RetrievalError("test_db_issue", f"local JSON database not found: {source_path}")
        try:
            raw = json.loads(source_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise RetrievalError("test_db_issue", f"local JSON database could not be read: {type(error).__name__}") from error
        if not isinstance(raw, list):
            raise RetrievalError("schema_issue", "local JSON database must contain a list")
        rows = [dict(item) for item in raw if isinstance(item, dict)]
        if len(rows) != len(raw):
            raise RetrievalError("schema_issue", "local JSON database contains a non-object row")
        return cls(rows, source_path=source_path, query_embedder=query_embedder)

    def _metadata(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = dict(row.get("metadata") or {})
        period_year, period_month = _period_parts(metadata.get("report_period"))
        metadata.setdefault("chunk_id", row.get("id"))
        metadata.setdefault("file_path", row.get("source_path", ""))
        metadata.setdefault("corp_name", metadata.get("corp_name", ""))
        metadata.setdefault("doc_group", metadata.get("source_group"))
        metadata.setdefault("doc_subtype", _doc_subtype(metadata))
        metadata.setdefault("base_year", period_year)
        metadata.setdefault("base_month", period_month)
        metadata.setdefault("rcept_dt", _text(metadata.get("disclosure_date")).replace("-", ""))
        metadata.setdefault("report_nm", metadata.get("document_type", ""))
        metadata.setdefault("is_correction", False)
        return metadata

    def _matches(self, row: dict[str, Any], request: SearchRequest) -> bool:
        metadata = self._metadata(row)
        corp_name = _text(metadata.get("corp_name"))
        corp_names = set(request.corp_names)
        if corp_names and corp_name not in corp_names:
            return False
        if request.sector and _text(metadata.get("sector")) != request.sector:
            return False
        doc_group = _text(metadata.get("doc_group"))
        allowed_groups = set(request.doc_group_candidates)
        if request.doc_group and doc_group != request.doc_group:
            return False
        if not request.doc_group and allowed_groups and doc_group not in allowed_groups:
            return False
        subtype = _text(metadata.get("doc_subtype"))
        allowed_subtypes = set(request.doc_subtype_candidates)
        if request.doc_subtype and subtype != request.doc_subtype:
            return False
        if not request.doc_subtype and allowed_subtypes and subtype not in allowed_subtypes:
            return False
        base_year = metadata.get("base_year")
        base_month = metadata.get("base_month")
        if request.base_years and base_year not in set(request.base_years):
            return False
        if request.base_months and base_month not in set(request.base_months):
            return False
        rcept_dt = _text(metadata.get("rcept_dt")).replace("-", "")
        if request.start_date and rcept_dt < request.start_date.replace("-", ""):
            return False
        if request.end_date and rcept_dt > request.end_date.replace("-", ""):
            return False
        if request.section_name and request.section_name not in _text(metadata.get("section_name")):
            return False
        if request.exclude_corp_name and request.exclude_corp_name in corp_name:
            return False
        if request.is_correction is not None and bool(metadata.get("is_correction", False)) != request.is_correction:
            return False
        if request.report_nm_contains:
            report_name = _text(metadata.get("report_nm"))
            if not any(token in report_name for token in request.report_nm_contains):
                return False
        return True

    def _filtered_rows(self, request: SearchRequest) -> list[dict[str, Any]]:
        rows = [row for row in self.rows if self._matches(row, request)]
        if request.sort_by_latest:
            rows.sort(key=lambda row: _text(self._metadata(row).get("rcept_dt")), reverse=True)
        if request.limit is not None:
            rows = rows[: max(0, request.limit)]
        return rows

    def _document(self, row: dict[str, Any], score: float | None) -> dict[str, Any]:
        metadata = self._metadata(row)
        identifier = _text(row.get("id") or metadata.get("chunk_id"))
        if not identifier:
            raise RetrievalError("schema_issue", "local JSON row has no stable id")
        return {
            "id": identifier,
            "source": _text(row.get("source_path") or metadata.get("file_path")),
            "text": _text(row.get("text")),
            "score": score,
            "metadata": metadata,
            "evidence_spans": [],
        }

    def search(self, request: SearchRequest) -> Stage2SearchResult:
        candidates = self._filtered_rows(request)
        query_id = str(uuid.uuid4())
        trace = [
            f"backend=json",
            f"source={self.source_path.name}",
            f"candidate_count={len(candidates)}",
        ]
        if not candidates:
            return Stage2SearchResult(
                query_id=query_id,
                search=request,
                status="not_found",
                retrieval_trace=[*trace, "status=not_found"],
                warnings=["검색 조건에 맞는 local JSON 문서가 없습니다."],
            )
        if self.query_embedder is None:
            raise RetrievalError(
                "embedding_unavailable",
                "local JSON semantic search requires a configured query embedding client",
            )
        try:
            query_vector = [float(value) for value in self.query_embedder(request.query)]
        except RetrievalError:
            raise
        except Exception as error:  # noqa: BLE001 - provider boundary
            raise RetrievalError("api_configuration", f"query embedding failed: {type(error).__name__}") from error
        scored: list[tuple[float, dict[str, Any]]] = []
        for row in candidates:
            raw_vector = row.get("embedding")
            if not isinstance(raw_vector, list) or not raw_vector:
                raise RetrievalError("schema_issue", f"document {row.get('id', '')} has no embedding")
            score = _cosine([float(value) for value in raw_vector], query_vector)
            scored.append((score, row))
        scored.sort(key=lambda item: item[0], reverse=True)
        documents = [self._document(row, score) for score, row in scored[: max(1, request.top_k)]]
        return Stage2SearchResult(
            query_id=query_id,
            search=request,
            documents=documents,
            retrieval_trace=[*trace, f"vector_ranked={len(documents)}", "status=ok"],
        )

    def keyword_search(self, request: SearchRequest) -> Stage2SearchResult:
        """Deterministic smoke-only search; the real E2E path never calls this."""

        query_tokens = set(_TOKEN_RE.findall(request.query.lower()))
        candidates = self._filtered_rows(request)
        ranked: list[tuple[int, dict[str, Any]]] = []
        for row in candidates:
            row_tokens = set(_TOKEN_RE.findall(_text(row.get("text")).lower()))
            ranked.append((len(query_tokens & row_tokens), row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        documents = [self._document(row, float(score)) for score, row in ranked[: max(1, request.top_k)]]
        return Stage2SearchResult(
            query_id=str(uuid.uuid4()),
            search=request,
            documents=documents,
            retrieval_trace=["backend=json", "mode=keyword_smoke", f"ranked={len(documents)}"],
            status="ok" if documents else "not_found",
            warnings=["keyword_smoke는 의미 검색 E2E 성공으로 집계하지 않습니다."],
        )
