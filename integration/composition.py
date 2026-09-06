"""Runtime factory for the canonical Interpreter→Validator graph."""

from __future__ import annotations

import os
from pathlib import Path

import config
from integration.cache import CacheRegistry, build_index_signature
from integration.graph import StageNodes
from integration.supervisor import build_planner_tool
from integration.clova import ClovaChatClient
from integration.rate_limit import ClovaRateLimiter
from integration.reranker import ClovaRerankerClient
from integration.readiness import (
    PARTIAL_INDEX_ISSUES,
    raise_if_invalid,
    validate_corpus_directory,
    validate_environment,
    validate_sqlite_path,
)
from integration.service import StagePipeline
from interpreter import build_interpreter_node
from retriever import (
    E5Embeddings,
    LocalHybridRetriever,
    RetrievalConfig,
    build_retriever_node,
    local_chroma,
    readonly_sqlite_engine,
)
from reasoner import build_reasoner_node
from reasoner.deterministic.calculation_planner import build_state_analysis_plan
from validator import build_validator_node
from validator.node import build_answer_regeneration_node
from reasoner.agents.answer import AnswerWriter


def _env_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 0)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(float(os.getenv(name, str(default))), 0.0)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _shared_clova_rate_limiter() -> ClovaRateLimiter:
    """Share one conservative process-local budget across all CLOVA APIs."""

    return ClovaRateLimiter(
        default_qpm=_env_int("CLOVA_RATE_LIMIT_QPM", 60),
        default_tpm=_env_int("CLOVA_RATE_LIMIT_TPM", 40000),
        min_interval=max(_env_float("CLOVA_CHAT_MIN_INTERVAL", 0.2), 0.2),
    )


def _embedding_function(
    settings: config.RetrieverSettings,
    *,
    cache=None,
    index_signature: str = "",
):
    """Dispatch to the configured embedding adapter.

    ``e5`` is the only accepted value because it is the model used to build the
    supplied vector index. This prevents a query/index embedding-space split.
    """

    embedding = settings.embedding
    if embedding == "e5":
        kwargs = {}
        if cache is not None:
            kwargs = {"cache": cache, "index_signature": index_signature}
        return E5Embeddings(**kwargs)
    raise RuntimeError(
        "unsupported Retriever embedding: "
        f"{embedding}; choose one of {', '.join(config.VALID_STAGE2_EMBEDDINGS)}"
    )


def _build_retriever(
    settings: config.RetrieverSettings,
    *,
    corpus: Path | None,
    cache=None,
    index_signature: str = "",
):
    """Open the read-only local Retriever retriever.

    Every path and connection string comes from :mod:`config`; nothing here
    recomputes a project-relative path or reads the environment directly.
    """

    # Local hybrid store: a SQLite file for metadata filtering + a local
    # Chroma persist directory for vector search. Both are opened without
    # creating or writing index files.
    if settings.mode != "local":
        raise RuntimeError(
            f"unsupported Retriever mode: {settings.mode}; "
            f"choose one of {', '.join(config.VALID_STAGE2_MODES)}"
        )

    raise_if_invalid(validate_sqlite_path(settings.sqlite_path))
    retriever = LocalHybridRetriever(
        engine=readonly_sqlite_engine(settings.sqlite_path),
        vectorstore=local_chroma(
            settings.chroma_path,
            embedding_function=_embedding_function(
                settings,
                cache=cache,
                index_signature=index_signature,
            ),
            collection_name=settings.chroma_collection,
            create_directory=False,
        ),
        collection_name=settings.chroma_collection,
        table_name=settings.sqlite_table,
        read_only=True,
        cache=cache,
        index_signature=index_signature,
    )

    tolerated = PARTIAL_INDEX_ISSUES if settings.allow_partial_index else frozenset()
    raise_if_invalid(retriever.readiness_issues(), tolerate=tolerated)
    if corpus is not None:
        raise_if_invalid(
            retriever.manifest_consistency_issues(corpus / "manifest.jsonl"),
            tolerate=tolerated,
        )
    return retriever


def build_pipeline() -> StagePipeline:
    """Compose the executable pipeline from environment-selected adapters."""

    settings = config.RetrieverSettings.from_env()
    raise_if_invalid(validate_environment(settings.mode))

    corpus = config.corpus_dir()
    if corpus is not None:
        raise_if_invalid(validate_corpus_directory(corpus))

    index_signature = build_index_signature(
        retriever_mode=settings.mode,
        sqlite_path=settings.sqlite_path,
        chroma_path=settings.chroma_path,
        backend_identity=(
            f"{settings.mode}:{settings.sqlite_table}:{settings.chroma_collection}"
        ),
    )
    cache = CacheRegistry.from_env(index_signature=index_signature)

    live_llm = _env_bool("CLOVA_LLM_ENABLED")
    interpreter_use_llm = _env_bool("STAGE1_USE_LLM")
    query_planner_llm_enabled = _env_bool("QUERY_PLANNER_LLM_ENABLED")
    reranker_enabled = _env_bool("CLOVA_RERANKER_ENABLED")
    clova_rate_limiter = _shared_clova_rate_limiter()
    chat_client = (
        ClovaChatClient(rate_limiter=clova_rate_limiter)
        if live_llm or interpreter_use_llm or query_planner_llm_enabled
        else None
    )
    reranker_client = (
        ClovaRerankerClient(rate_limiter=clova_rate_limiter)
        if reranker_enabled
        else None
    )

    interpreter = build_interpreter_node(
        corpus_dir=corpus,
        llm_client=chat_client if interpreter_use_llm else None,
        use_llm=interpreter_use_llm,
    )

    retriever = _build_retriever(
        settings,
        corpus=corpus,
        cache=cache,
        index_signature=index_signature,
    )
    reranker_candidate_limit = max(
        _env_int("CLOVA_RERANKER_CANDIDATE_LIMIT", 100), 1
    )
    reranker_config = RetrievalConfig(
        candidate_limit=1000,
        branch_limit=100,
        final_limit=100 if reranker_enabled else 200,
        reranker=reranker_client,
        reranker_candidate_limit=(
            reranker_candidate_limit if reranker_enabled else None
        ),
    )
    regeneration = (
        build_answer_regeneration_node(answer_client=chat_client)
        if live_llm and chat_client is not None
        else None
    )
    calculation_planner = build_planner_tool(
        lambda state: build_state_analysis_plan(
            state,
            llm_client=chat_client if query_planner_llm_enabled else None,
            llm_enabled=query_planner_llm_enabled,
        )
    )

    return StagePipeline(StageNodes(
        interpreter=interpreter,
        # Keep all metadata-filtered chunks available so Reasoner can recover
        # aggregate rows that rank below subsidiary or segment rows in the
        # hybrid score. Retrieval prompt compaction still bounds what is sent
        # to external LLMs.
        retriever=build_retriever_node(
            retriever=retriever,
            config=reranker_config,
            cache=cache,
        ),
        reasoner=build_reasoner_node(
            answer_writer=AnswerWriter(chat_client if live_llm else None),
            cache=cache,
        ),
        validator=build_validator_node(
            validator_client=chat_client if live_llm else None
        ),
        calculation_planner=calculation_planner,
        answer_regeneration=regeneration,
    ))


__all__ = ["build_pipeline"]
