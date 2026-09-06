"""LangGraph boundary for the retrieval-only Retriever."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable

from retriever.retrieval import RetrievalConfig, RetrieverProtocol, build_search_query, retrieve


def build_retriever_node(
    *,
    retriever: RetrieverProtocol,
    config: RetrievalConfig = RetrievalConfig(),
    cache: Any | None = None,
) -> Callable[[Mapping[str, Any]], dict[str, Any]]:
    """Build a node that writes only Retriever-owned State fields."""

    # LocalHybridRetriever uses this registry for SQL candidate caching.  The
    # optional capability keeps direct test/custom retrievers compatible.
    set_cache = getattr(retriever, "set_cache", None)
    if cache is not None and callable(set_cache):
        set_cache(cache)

    def _plan_requirements(state: Mapping[str, Any]) -> list[dict[str, Any]]:
        plan = state.get("analysis_plan")
        if not isinstance(plan, Mapping) or plan.get("status") != "ready":
            return []
        requirements = plan.get("requirements")
        return [dict(item) for item in requirements if isinstance(item, Mapping)] if isinstance(requirements, list) else []

    def _requirement_intent(base_intent: Mapping[str, Any], requirement: Mapping[str, Any]) -> dict[str, Any]:
        sub_intent = dict(base_intent)
        metric = str(requirement.get("metric", "")).strip()
        companies = [str(item) for item in requirement.get("companies", []) if str(item).strip()]
        periods = [str(item) for item in requirement.get("periods", []) if str(item).strip()]
        manifest = dict(requirement.get("manifest_filter", {})) if isinstance(requirement.get("manifest_filter"), Mapping) else {}
        if companies:
            manifest["corp_names"] = companies
        if periods:
            years = [int(period[:4]) for period in periods if str(period)[:4].isdigit()]
            if years:
                manifest["base_years"] = years
        sub_intent["metric"] = metric
        sub_intent["manifest_filter"] = manifest
        sub_intent["time"] = {"years": [str(year) for year in manifest.get("base_years") or []] or periods}
        sub_intent["calculation"] = {}
        sub_intent["query_plan"] = []
        return sub_intent

    def retriever_node(state: Mapping[str, Any]) -> dict[str, Any]:
        intent = state.get("intent") if isinstance(state.get("intent"), Mapping) else {}
        requirements = _plan_requirements(state)
        if requirements:
            subresults: list[dict[str, Any]] = []
            documents_by_id: dict[str, dict[str, Any]] = {}
            warnings: list[str] = []
            trace: list[str] = []
            search_queries: dict[str, str] = {}
            for item in requirements:
                requirement_id = str(item.get("id") or f"requirement-{len(subresults) + 1}")
                sub_intent = _requirement_intent(intent, item)
                query = build_search_query(str(state.get("question", "")), sub_intent)
                if int(state.get("retry_num", 0) or 0) > 0 and state.get("search_query"):
                    query = f"{query} {state['search_query']}"
                search_queries[requirement_id] = query
                result = retrieve(
                    question_id=f"{state.get('question_id', '')}:{requirement_id}",
                    question=str(state.get("question", "")),
                    intent=sub_intent,
                    route=str(state.get("route", "unanswerable")),
                    retriever=retriever,
                    config=config,
                    search_query=query,
                )
                result["subquery_id"] = requirement_id
                result["requirement_id"] = requirement_id
                result["subquery"] = dict(item)
                subresults.append(result)
                warnings.extend(str(value) for value in result.get("warnings", []))
                trace.extend(f"{requirement_id}:{value}" for value in result.get("retrieval_trace", []))
                for document in result.get("cited_documents", []):
                    identifier = str(document.get("id") or document.get("doc_id") or "")
                    if identifier and identifier not in documents_by_id:
                        documents_by_id[identifier] = dict(document)

            statuses = [str(item.get("status", "error")) for item in subresults]
            all_documents = list(documents_by_id.values())
            if all(status == "ok" for status in statuses) and statuses:
                status = "ok"
            elif all_documents:
                status = "partial_success"
            elif any(status == "rate_limited" for status in statuses):
                status = "rate_limited"
            elif statuses and all(status == "not_found" for status in statuses):
                status = "not_found"
            else:
                status = "error"
            result = {
                "query_id": str(state.get("question_id", "")),
                "status": status,
                "documents": all_documents,
                "cited_documents": all_documents,
                "subresults": subresults,
                "search_queries": search_queries,
                "retrieval_trace": trace,
                "warnings": warnings,
                "provider_status": {
                    "subqueries": [
                        item.get("provider_status", {})
                        for item in subresults
                        if item.get("provider_status")
                    ]
                },
            }
            return {
                "retriever_result": result,
                "documents": result["cited_documents"],
                "retry_num": int(state.get("retry_num", 0) or 0),
                "search_attempts": int(state.get("search_attempts", 0) or 0) + 1,
                "search_query": next(iter(search_queries.values()), ""),
                "search_queries": search_queries,
            }
        query_plan = intent.get("query_plan") if isinstance(intent, Mapping) else None
        if isinstance(query_plan, list) and len(query_plan) > 1:
            subresults: list[dict[str, Any]] = []
            documents_by_id: dict[str, dict[str, Any]] = {}
            warnings: list[str] = []
            trace: list[str] = []
            search_queries: dict[str, str] = {}
            for item in query_plan:
                if not isinstance(item, Mapping):
                    continue
                subquery_id = str(item.get("subquery_id") or f"subquery-{len(subresults) + 1}")
                sub_intent = dict(intent)
                for key in ("metric", "question_type", "calculation", "basis", "time", "manifest_filter"):
                    if key in item:
                        sub_intent[key] = item[key]
                sub_intent["query_plan"] = []
                query = build_search_query(str(state.get("question", "")), sub_intent)
                search_queries[subquery_id] = query
                result = retrieve(
                    question_id=f"{state.get('question_id', '')}:{subquery_id}",
                    question=str(state.get("question", "")),
                    intent=sub_intent,
                    route=str(state.get("route", "unanswerable")),
                    retriever=retriever,
                    config=config,
                    search_query=query,
                )
                result["subquery_id"] = subquery_id
                result["subquery"] = dict(item)
                subresults.append(result)
                warnings.extend(str(value) for value in result.get("warnings", []))
                trace.extend(f"{subquery_id}:{value}" for value in result.get("retrieval_trace", []))
                for document in result.get("cited_documents", []):
                    identifier = str(document.get("id") or document.get("doc_id") or "")
                    if identifier and identifier not in documents_by_id:
                        documents_by_id[identifier] = dict(document)

            statuses = [str(item.get("status", "error")) for item in subresults]
            all_documents = list(documents_by_id.values())
            if all(status == "ok" for status in statuses) and statuses:
                status = "ok"
            elif all_documents:
                status = "partial_success"
            elif any(status == "rate_limited" for status in statuses):
                status = "rate_limited"
            elif statuses and all(status == "not_found" for status in statuses):
                status = "not_found"
            else:
                status = "error"
            result = {
                "query_id": str(state.get("question_id", "")),
                "status": status,
                "documents": all_documents,
                "cited_documents": all_documents,
                "subresults": subresults,
                "search_queries": search_queries,
                "retrieval_trace": trace,
                "warnings": warnings,
                "provider_status": {
                    "subqueries": [
                        item.get("provider_status", {})
                        for item in subresults
                        if item.get("provider_status")
                    ]
                },
            }
            return {
                "retriever_result": result,
                "documents": result["cited_documents"],
                "retry_num": int(state.get("retry_num", 0) or 0),
                "search_attempts": int(state.get("search_attempts", 0) or 0) + 1,
                "search_query": search_queries.get("subquery-1", ""),
                "search_queries": search_queries,
            }
        query = (
            str(state.get("search_query"))
            if state.get("search_query") and state.get("search_query") != state.get("original_question", state.get("question"))
            else build_search_query(
                str(state.get("question", "")),
                state.get("intent") if isinstance(state.get("intent"), Mapping) else {},
            )
        )
        result = retrieve(
            question_id=str(state.get("question_id", "")),
            question=str(state.get("question", "")),
            intent=intent,
            route=str(state.get("route", "unanswerable")),
            retriever=retriever,
            config=config,
            search_query=query,
        )
        return {
            "retriever_result": result,
            "documents": result["cited_documents"],
            "retry_num": int(state.get("retry_num", 0) or 0),
            "search_attempts": int(state.get("search_attempts", 0) or 0) + 1,
            "search_query": query,
        }

    return retriever_node


__all__ = ["build_retriever_node"]
