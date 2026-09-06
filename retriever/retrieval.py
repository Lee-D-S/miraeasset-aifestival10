"""Dependency-free Retriever retrieval core.

The production SQLite/Chroma adapters can be added later without changing the
Retriever node contract.  This module deliberately does not call an LLM or load
an embedding model.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from integration.rate_limit import is_rate_limit_error, rate_limit_event


_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)
_SPACED_HANGUL_RE = re.compile(r"(?<![가-힣])(?:[가-힣]\s+){2,}[가-힣](?![가-힣])")
_KOREAN_PARTICLES = ("으로", "에서", "에게", "까지", "부터", "은", "는", "이", "가", "을", "를", "의", "과", "와", "도", "로")


def _text(value: Any) -> str:
    return unicodedata.normalize("NFC", str(value or "")).strip()


def _tokens(value: Any) -> set[str]:
    raw = _text(value)
    compact = _SPACED_HANGUL_RE.sub(lambda match: re.sub(r"\s+", "", match.group(0)), raw)
    tokens = {token.lower() for token in _TOKEN_RE.findall(raw + " " + compact)}
    for token in tuple(tokens):
        for particle in _KOREAN_PARTICLES:
            if token.endswith(particle) and len(token) > len(particle) + 1:
                tokens.add(token[: -len(particle)])
                break
    return tokens


def _metadata(document: Mapping[str, Any]) -> Mapping[str, Any]:
    value = document.get("metadata")
    return value if isinstance(value, Mapping) else document


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _normalized_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        if value.strip().lower() in {"true", "1", "yes"}:
            return True
        if value.strip().lower() in {"false", "0", "no"}:
            return False
    return bool(value)


def _date_value(value: Any) -> str:
    return _text(value).replace("-", "")


def matches_manifest_filter(document: Mapping[str, Any], manifest_filter: Mapping[str, Any]) -> bool:
    """Return whether one document satisfies Interpreter's manifest filter."""

    metadata = _metadata(document)
    corp_names = {_text(item) for item in _as_list(manifest_filter.get("corp_names")) if _text(item)}
    corp_name = _text(metadata.get("corp_name"))
    excluded_corp_names = {
        _text(item) for item in _as_list(manifest_filter.get("exclude_corp_names")) if _text(item)
    }
    if excluded_corp_names and corp_name in excluded_corp_names:
        return False
    if corp_names and corp_name not in corp_names:
        return False

    sector = _text(manifest_filter.get("sector"))
    if sector and sector not in _text(metadata.get("sector")):
        return False

    doc_group = _text(manifest_filter.get("doc_group"))
    doc_groups = {_text(item) for item in _as_list(manifest_filter.get("doc_group_candidates"))}
    if doc_group and _text(metadata.get("doc_group")) != doc_group:
        return False
    if not doc_group and doc_groups and _text(metadata.get("doc_group")) not in doc_groups:
        return False

    doc_subtype = _text(manifest_filter.get("doc_subtype"))
    doc_subtypes = {_text(item) for item in _as_list(manifest_filter.get("doc_subtype_candidates"))}
    actual_subtype = _text(metadata.get("doc_subtype"))
    if doc_subtype and actual_subtype != doc_subtype:
        return False
    if not doc_subtype and doc_subtypes and actual_subtype not in doc_subtypes:
        return False

    years = {str(item) for item in _as_list(manifest_filter.get("base_years"))}
    months = {str(item) for item in _as_list(manifest_filter.get("base_months"))}
    if years and str(metadata.get("base_year")) not in years:
        return False
    if months and str(metadata.get("base_month")) not in months:
        return False

    received = _date_value(metadata.get("rcept_dt"))
    start = _date_value(manifest_filter.get("rcept_from"))
    end = _date_value(manifest_filter.get("rcept_to"))
    if start and (not received or received < start):
        return False
    if end and (not received or received > end):
        return False

    correction = _normalized_bool(manifest_filter.get("is_correction"))
    if correction is not None and _normalized_bool(metadata.get("is_correction")) != correction:
        return False

    report_terms = [_text(item).lower() for item in _as_list(manifest_filter.get("report_nm_contains"))]
    report_name = _text(metadata.get("report_nm")).lower()
    if report_terms and not any(term in report_name for term in report_terms):
        return False
    return True


_INTERPRETER_METRIC_LABELS = {
    "revenue": ("매출액", "매출"),
    "operating_profit": ("영업이익",),
    "net_income": ("당기순이익", "순이익"),
    "total_assets": ("자산총계", "부채비율", "자기자본비율"),
}
_METRIC_NARRATIVE_TERMS = {
    "revenue": ("당사의 매출",),
}
_PLAN_TABLE_CUES = ("잔여 계획기간", "계획기간", "합계 구간", "당기 이행연도")
_COMPANY_TOTAL_CUES = ("당사의 매출", "매출액은")


def _metric_query_terms(question: str, metric: str) -> list[str]:
    """Map Interpreter English metric keys to DART labels used in the index."""

    compact = question.replace(" ", "")
    if metric == "total_assets":
        if "자기자본비율" in compact:
            return ["자기자본비율"]
        if "부채비율" in compact:
            return ["부채비율"]
        return ["자산총계"]
    return [label for label in _INTERPRETER_METRIC_LABELS.get(metric, ()) if label not in compact]


def _requested_years(intent: Mapping[str, Any]) -> list[str]:
    time = intent.get("time") if isinstance(intent.get("time"), Mapping) else {}
    years = [_year_token(year) for year in _as_list(time.get("years"))]
    years = [year for year in years if year]
    if years:
        return list(dict.fromkeys(years))
    manifest = intent.get("manifest_filter") if isinstance(intent.get("manifest_filter"), Mapping) else {}
    return list(dict.fromkeys(
        year for value in _as_list(manifest.get("base_years"))
        if (year := _year_token(value))
    ))


def _year_token(value: Any) -> str:
    text = _text(value)
    return text[:4] if len(text) >= 4 and text[:4].isdigit() else ""


def _document_year(document: Mapping[str, Any]) -> str:
    metadata = _metadata(document)
    year = _text(metadata.get("base_year"))
    if len(year) >= 4 and year[:4].isdigit():
        return year[:4]
    period = _text(metadata.get("report_period"))
    return period[:4] if len(period) >= 4 and period[:4].isdigit() else ""


def build_search_query(question: str, intent: Mapping[str, Any]) -> str:
    """Build a deterministic search query without reinterpreting the question."""

    normalized = _text(intent.get("normalized_question")) or _text(question)
    parts = [normalized]
    metric = _text(intent.get("metric"))
    compact = normalized.replace(" ", "")
    for term in _metric_query_terms(normalized, metric):
        if term and term not in compact and term not in " ".join(parts):
            parts.append(term)
    basis = _text(intent.get("basis"))
    if basis and basis not in normalized:
        parts.append(basis)
    scope_term = {"segment": "사업부문별", "product": "제품별", "region": "지역별"}.get(intent.get("aggregation_scope"))
    if scope_term and scope_term not in normalized:
        parts.append(scope_term)
    for year in _requested_years(intent):
        if year not in " ".join(parts):
            parts.append(year)
    for term in _METRIC_NARRATIVE_TERMS.get(metric, ()):
        if term and term not in compact and term not in " ".join(parts):
            parts.append(term)
    return " ".join(part for part in parts if part)


def _looks_like_plan_table(document: Mapping[str, Any]) -> bool:
    return any(cue in _text(document.get("text")) for cue in _PLAN_TABLE_CUES)


def _looks_like_company_total(document: Mapping[str, Any]) -> bool:
    return any(cue in _text(document.get("text")) for cue in _COMPANY_TOTAL_CUES)


def _company_total_rank(document: Mapping[str, Any]) -> tuple:
    score = float(document.get("hybrid_score") or document.get("score") or 0.0)
    return (_looks_like_plan_table(document), not _looks_like_company_total(document), -score)


def _ensure_company_total_in_year(
    hits: Sequence[Mapping[str, Any]],
    subset: Sequence[Mapping[str, Any]],
    limit: int,
) -> list[Mapping[str, Any]]:
    selected = [dict(document) for document in hits][:limit]
    if any(_looks_like_company_total(document) for document in selected):
        return selected
    replacement = next((dict(document) for document in subset if _looks_like_company_total(document)), None)
    if replacement is None:
        return selected
    for index, document in enumerate(selected):
        if _looks_like_plan_table(document):
            selected[index] = replacement
            return selected[:limit]
    if len(selected) < limit:
        selected.append(replacement)
    elif selected:
        selected[-1] = replacement
    else:
        selected = [replacement]
    return selected[:limit]


_SUMMARY_SECTION_CUES = ("요약재무", "경영진단", "재무상태", "영업실적")
_TABLE_METRIC_METRICS = frozenset({"operating_profit", "net_income"})


def _looks_like_summary_table(document: Mapping[str, Any]) -> bool:
    """A 요약재무정보 / MD&A financial-results chunk that kept its per-row JSON.

    Those tables list every amount metric for the last three fiscal years with
    a ``제NN기`` header, so one chunk answers a "최근 3년 X 추이" question.
    """

    metadata = _metadata(document)
    section = _text(metadata.get("section_name"))
    if not any(cue in section for cue in _SUMMARY_SECTION_CUES):
        return False
    return bool(
        _text(document.get("raw_json_content")) or _text(metadata.get("raw_json_content"))
    )


def _ensure_summary_table_in_year(
    hits: Sequence[Mapping[str, Any]],
    subset: Sequence[Mapping[str, Any]],
    limit: int,
) -> list[Mapping[str, Any]]:
    selected = [dict(document) for document in hits][:limit]
    if any(_looks_like_summary_table(document) for document in selected):
        return selected
    replacement = next(
        (dict(document) for document in subset if _looks_like_summary_table(document)),
        None,
    )
    if replacement is None:
        return selected
    if len(selected) < limit:
        selected.append(replacement)
    elif selected:
        selected[-1] = replacement
    else:
        selected = [replacement]
    return selected[:limit]


def _diversify_by_year(
    documents: Sequence[Mapping[str, Any]],
    years: Sequence[str],
    limit: int,
    *,
    metric: str | None = None,
) -> list[Mapping[str, Any]]:
    """Keep top hits while guaranteeing one document per requested year when possible."""

    ranked = [dict(document) for document in documents]
    if limit <= 0:
        return []
    if len(years) <= 1:
        return ranked[:limit]
    prefer_company_total = str(metric or "") == "revenue"
    picked: list[Mapping[str, Any]] = []
    used: set[str] = set()
    for year in years:
        year_docs = [
            document
            for document in ranked
            if _document_id(document) not in used and _document_year(document) == str(year)
        ]
        if not year_docs:
            continue
        # The company-total preference targets the revenue "당사의 매출" sentence.
        # For other metrics keep the top-ranked hit of the year unchanged.
        chosen = min(year_docs, key=_company_total_rank) if prefer_company_total else year_docs[0]
        picked.append(chosen)
        used.add(_document_id(chosen))
    rank_index = {_document_id(document): index for index, document in enumerate(ranked)}
    picked.sort(key=lambda document: rank_index.get(_document_id(document), len(ranked)))
    for document in ranked:
        if len(picked) >= limit:
            break
        identifier = _document_id(document)
        if identifier not in used:
            picked.append(document)
            used.add(identifier)
    return picked[:limit]


def _search_results_by_year(
    search,
    query: str,
    candidates: Sequence[Mapping[str, Any]],
    years: Sequence[str],
    limit: int,
    *,
    metric: str | None = None,
) -> list[Mapping[str, Any]]:
    if limit <= 0:
        return []
    if len(years) <= 1:
        return list(search(query, candidates, limit))
    per_year = max(limit // len(years), 1)
    force_company_total = str(metric or "") == "revenue"
    force_summary_table = str(metric or "") in _TABLE_METRIC_METRICS
    seen: set[str] = set()
    merged: list[Mapping[str, Any]] = []
    for year in years:
        subset = [document for document in candidates if _document_year(document) == str(year)]
        if not subset:
            continue
        hits = list(search(query, subset, per_year))
        if force_company_total:
            # Only the revenue question needs the "당사의 매출" company-total row
            # forced past investment-plan tables; other metrics keep the raw hits.
            hits = _ensure_company_total_in_year(hits, subset, per_year)
        if force_summary_table:
            # operating_profit / net_income totals live in the 요약재무정보 or
            # MD&A financial-results table, not a prose sentence. Guarantee one
            # such chunk per year so the per-year keyword cut cannot drop it.
            hits = _ensure_summary_table_in_year(hits, subset, per_year)
        for document in hits:
            identifier = _document_id(document)
            if identifier and identifier not in seen:
                seen.add(identifier)
                merged.append(document)
    if len(merged) < limit:
        for document in search(query, candidates, limit):
            identifier = _document_id(document)
            if identifier and identifier not in seen:
                seen.add(identifier)
                merged.append(document)
            if len(merged) >= limit:
                break
    return merged[:limit]


class RetrieverProtocol(Protocol):
    """Backend contract for metadata, keyword, and vector retrieval."""

    def filter_candidates(
        self,
        manifest_filter: Mapping[str, Any],
        limit: int,
        *,
        query: str | None = None,
    ) -> list[Mapping[str, Any]]:
        ...

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...


class Reranker(Protocol):
    """Rank merged candidates independently from keyword/vector retrieval."""

    def rerank(self, query: str, documents: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        ...


class DeterministicReranker:
    """Explicit test/local reranker; production can inject CLOVA here."""

    def rerank(self, query: str, documents: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        del query
        return sorted(
            (dict(document) for document in documents),
            key=lambda document: (-float(document.get("hybrid_score", document.get("score", 0.0))), _document_id(document)),
        )[:limit]


@dataclass(frozen=True)
class RetrievalConfig:
    candidate_limit: int = 50
    branch_limit: int = 20
    final_limit: int = 8
    keyword_weight: float = 0.5
    vector_weight: float = 0.5
    reranker: Reranker | None = None
    reranker_candidate_limit: int | None = None


class InMemoryRetriever:
    """Small deterministic backend for tests and local contract development."""

    def __init__(self, documents: Sequence[Mapping[str, Any]], vector_scores: Mapping[str, float] | None = None):
        self.documents = [dict(document) for document in documents]
        self.vector_scores = dict(vector_scores or {})

    @staticmethod
    def _id(document: Mapping[str, Any]) -> str:
        return _text(document.get("id") or document.get("chunk_id") or document.get("doc_id"))

    @staticmethod
    def _content(document: Mapping[str, Any]) -> str:
        return _text(document.get("text") or document.get("page_content") or document.get("text_content"))

    def filter_candidates(
        self,
        manifest_filter: Mapping[str, Any],
        limit: int,
        *,
        query: str | None = None,
    ) -> list[Mapping[str, Any]]:
        del query  # in-memory backend ranks the full document set; no pre-filter narrowing
        years = [str(year) for year in _as_list(manifest_filter.get("base_years")) if str(year).strip()]
        if len(years) > 1 and limit > 0:
            per_year = max(limit // len(years), 1)
            seen: set[str] = set()
            merged: list[Mapping[str, Any]] = []
            for year in years:
                year_filter = {**manifest_filter, "base_years": [year]}
                matched = [
                    document
                    for document in self.documents
                    if matches_manifest_filter(document, year_filter)
                ]
                for document in matched[:per_year]:
                    identifier = self._id(document)
                    if identifier and identifier not in seen:
                        seen.add(identifier)
                        merged.append(document)
            return merged[:limit]
        matched = [document for document in self.documents if matches_manifest_filter(document, manifest_filter)]
        return matched[:limit]

    def keyword_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        query_tokens = _tokens(query)
        scored = []
        for document in candidates:
            content_tokens = _tokens(self._content(document))
            score = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            if score > 0:
                scored.append((score, self._id(document), document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(document), "keyword_score": score} for score, _, document in scored[:limit]]

    def vector_search(self, query: str, candidates: Sequence[Mapping[str, Any]], limit: int) -> list[Mapping[str, Any]]:
        del query
        scored = []
        for document in candidates:
            identifier = self._id(document)
            score = float(self.vector_scores.get(identifier, document.get("vector_score", 0.0)))
            if score > 0:
                scored.append((score, identifier, document))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{**dict(document), "vector_score": score} for score, _, document in scored[:limit]]


def _document_id(document: Mapping[str, Any]) -> str:
    return _text(document.get("id") or document.get("chunk_id") or document.get("doc_id") or document.get("document_id"))


def _normalize_document(document: Mapping[str, Any], score: float, sources: list[str]) -> dict[str, Any]:
    raw = dict(document)
    metadata = dict(raw.get("metadata")) if isinstance(raw.get("metadata"), Mapping) else {}
    for key, value in raw.items():
        if key not in metadata and key not in {"text", "page_content", "text_content", "metadata"}:
            metadata[key] = value
    identifier = _document_id(raw)
    text = _text(raw.get("text") or raw.get("page_content") or raw.get("text_content"))
    return {
        "id": identifier,
        "doc_id": _text(raw.get("doc_id") or metadata.get("doc_id") or identifier),
        "chunk_id": _text(raw.get("chunk_id") or metadata.get("chunk_id") or identifier),
        "source": _text(raw.get("source") or raw.get("file_path") or metadata.get("source") or metadata.get("file_path")),
        "text": text,
        "score": round(score, 6),
        "metadata": metadata,
        "evidence_spans": list(raw.get("evidence_spans") or []),
        "match_sources": sources,
    }


def retrieve(
    *,
    question_id: str,
    question: str,
    intent: Mapping[str, Any],
    route: str,
    retriever: RetrieverProtocol,
    config: RetrievalConfig = RetrievalConfig(),
    search_query: str | None = None,
) -> dict[str, Any]:
    """Run Retriever and return the canonical shared-state envelope."""

    empty = {
        "query_id": _text(question_id),
        "documents": [],
        "cited_documents": [],
        "retrieval_trace": [],
        "warnings": [],
        "provider_status": {},
    }
    if route != "ok":
        return {**empty, "status": "skipped", "retrieval_trace": [f"route={route}"]}

    manifest_filter = intent.get("manifest_filter")
    if not isinstance(manifest_filter, Mapping):
        return {**empty, "status": "error", "warnings": ["Interpreter manifest_filter가 없습니다."]}

    query = _text(search_query) or build_search_query(question, intent)
    metric = _text(intent.get("metric"))
    candidates = retriever.filter_candidates(
        manifest_filter, config.candidate_limit, query=query
    )
    years = _requested_years(intent)
    keyword_results = _search_results_by_year(
        retriever.keyword_search, query, candidates, years, config.branch_limit, metric=metric,
    )
    try:
        vector_results = _search_results_by_year(
            retriever.vector_search, query, candidates, years, config.branch_limit, metric=metric,
        )
    except Exception as error:  # provider/backend boundary; never fake semantic success
        provider_status = {}
        if is_rate_limit_error(error):
            provider_status = rate_limit_event(
                error,
                operation="embedding",
                client=getattr(retriever, "query_embedder", None),
            )
            classification = "rate_limited"
        else:
            classification = getattr(error, "classification", "embedding_unavailable")
        return {
            **empty,
            "status": str(classification),
            "retrieval_trace": [f"query={query}", f"candidate_count={len(candidates)}"],
            "warnings": [str(error)],
            "provider_status": provider_status,
        }

    merged: dict[str, dict[str, Any]] = {}
    for branch, results in (("keyword", keyword_results), ("vector", vector_results)):
        for rank, result in enumerate(results):
            identifier = _document_id(result)
            if not identifier:
                continue
            item = merged.setdefault(identifier, {"raw": dict(result), "sources": set(), "keyword": 0.0, "vector": 0.0})
            item["sources"].add(branch)
            if branch == "keyword":
                item["keyword"] = max(float(item["keyword"]), float(result.get("keyword_score", 0.0)))
            else:
                item["vector"] = max(float(item["vector"]), float(result.get("vector_score", 0.0)))
            item["raw"].setdefault("_rank_" + branch, rank)

    merged_documents = []
    for item in merged.values():
        hybrid_score = config.keyword_weight * item["keyword"] + config.vector_weight * item["vector"]
        document = _normalize_document(item["raw"], hybrid_score, sorted(item["sources"]))
        document["hybrid_score"] = hybrid_score
        merged_documents.append(document)
    pool_limit = max(config.final_limit, config.branch_limit)
    warnings: list[str] = []
    provider_status: dict[str, Any] = {}
    retrieval_trace = [
        f"query={query}",
        f"candidate_count={len(candidates)}",
        f"keyword_count={len(keyword_results)}",
        f"vector_count={len(vector_results)}",
    ]
    deterministic_reranker = DeterministicReranker()
    if config.reranker is None:
        ranked = deterministic_reranker.rerank(query, merged_documents, pool_limit)
    else:
        # Send only the deterministic top-N candidates to the provider. If the
        # provider fails, rerank the complete merged set locally and then apply
        # the configured final limit.
        deterministic_candidates = deterministic_reranker.rerank(
            query, merged_documents, len(merged_documents)
        )
        candidate_limit = max(
            int(config.reranker_candidate_limit or pool_limit), 1
        )
        reranker_documents = deterministic_candidates[:candidate_limit]
        try:
            ranked = list(
                config.reranker.rerank(query, reranker_documents, pool_limit)
            )
            if merged_documents and not ranked:
                raise ValueError("reranker returned no documents")
            provider_status = dict(
                getattr(config.reranker, "last_provider_status", {}) or {}
            )
        except Exception as error:  # provider boundary: deterministic fallback
            ranked = deterministic_reranker.rerank(
                query, merged_documents, pool_limit
            )
            warnings.append(f"reranker_fallback: {type(error).__name__}")
            if is_rate_limit_error(error):
                provider_status = rate_limit_event(
                    error,
                    operation="reranker",
                    client=config.reranker,
                )
            else:
                provider_status = dict(
                    getattr(config.reranker, "last_provider_status", {}) or {}
                )
                provider_status.setdefault("status", "provider_error")
                provider_status.setdefault("operation", "reranker")
                provider_status.setdefault("error_type", type(error).__name__)
        suggested_queries = getattr(config.reranker, "suggested_queries", [])
        if isinstance(suggested_queries, list) and suggested_queries:
            retrieval_trace.append(
                f"reranker_suggested_queries_count={len(suggested_queries)}"
            )
    documents = _diversify_by_year(ranked, _requested_years(intent), config.final_limit, metric=metric)
    cited = documents
    status = "ok" if cited else "not_found"
    retrieval_trace.extend(
        [
            f"reranker={'clova' if config.reranker is not None else 'deterministic'}",
            f"merged_count={len(merged_documents)}",
            f"cited_count={len(cited)}",
        ]
    )
    return {
        "query_id": _text(question_id),
        "status": status,
        "documents": documents,
        "cited_documents": cited,
        "retrieval_trace": retrieval_trace,
        "warnings": warnings,
        "provider_status": provider_status,
    }


__all__ = [
    "InMemoryRetriever",
    "RetrievalConfig",
    "RetrieverProtocol",
    "build_search_query",
    "matches_manifest_filter",
    "retrieve",
]
