from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from stage1 import build_intent
from stage3.api_contract import to_submission_response
from stage3.contracts import Stage3Result
from stage3.service import Stage3Service

from stage1 import CorpusIndex
from stage2.embedding import ClovaQueryEmbedding
from stage2.json_repository import JsonStage2Repository
from stage2.llm import DependencyConfigurationError, ProviderConfigurationError, build_stage2_chat_model
from .local_index import LocalJsonCorpusIndex
from stage2.production_repository import ProductionStage2Repository
from stage2.stage2_agent import Stage2Agent
from stage2.stage2_repository import RetrievalError


class IntegrationFailure(RuntimeError):
    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


_PROVIDER_CONNECTION_ERRORS = {"APIConnectionError", "APITimeoutError"}
_PROVIDER_AUTH_ERRORS = {"AuthenticationError", "PermissionDeniedError"}
_PROVIDER_LIMIT_ERRORS = {"RateLimitError"}


def _safe_exception_detail(error: Exception, *, limit: int = 500) -> str:
    """Keep provider diagnostics while preventing credentials from entering traces."""

    parts = [" ".join(str(error).split())]
    cause = error.__cause__ or error.__context__
    if cause is not None and str(cause):
        parts.append(f"cause={type(cause).__name__}: {' '.join(str(cause).split())}")
    detail = "; ".join(part for part in parts if part)
    for env_name in ("CLOVA_API_KEY", "CLOVASTUDIO_API_KEY", "CLOVASTUDIO_APIGW_API_KEY", "OPENAI_API_KEY"):
        secret = os.getenv(env_name, "").strip()
        if secret:
            detail = detail.replace(secret, "***")
    return detail[:limit] or type(error).__name__


def _stage2_failure(error: Exception) -> IntegrationFailure:
    error_name = type(error).__name__
    if error_name in _PROVIDER_CONNECTION_ERRORS:
        classification = "provider_connection"
    elif error_name in _PROVIDER_AUTH_ERRORS:
        classification = "api_configuration"
    elif error_name in _PROVIDER_LIMIT_ERRORS:
        classification = "provider_rate_limit"
    else:
        classification = "integration_bug"
    return IntegrationFailure(
        classification,
        f"Stage2 workflow failed: {error_name}: {_safe_exception_detail(error)}",
    )


@dataclass
class E2ERun:
    question_id: str
    question: str
    status: str
    intent: dict[str, Any]
    stage2_result: dict[str, Any]
    stage3_result: Stage3Result
    response: dict[str, str]


def _flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _default_json_path() -> Path:
    return Path(__file__).resolve().parents[2] / "test_data" / "disclosure_clova_local.json"


def _resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path


class Stage123Application:
    """Composition root for the real Stage1 → Stage2 → Stage3 flow."""

    def __init__(
        self,
        *,
        index: Any,
        repository: Any,
        model_factory: Callable[[], Any] = build_stage2_chat_model,
        execution_mode: str = "stdlib",
        stage1_use_llm: bool = False,
    ) -> None:
        self.index = index
        self.repository = repository
        self.model_factory = model_factory
        self.execution_mode = execution_mode
        self.stage1_use_llm = stage1_use_llm
        self.stage3_service = Stage3Service(execution_mode=execution_mode)

    @classmethod
    def from_environment(cls) -> "Stage123Application":
        backend = os.getenv("E2E_DB_BACKEND", "json").strip().lower()
        execution_mode = os.getenv("STAGE3_EXECUTION_MODE", "stdlib").strip().lower()
        if backend == "json":
            json_path = _resolve_path(os.getenv("LOCAL_JSON_DB_PATH"), _default_json_path())
            has_embedding_key = bool(
                os.getenv("CLOVA_API_KEY", "").strip()
                or os.getenv("CLOVASTUDIO_API_KEY", "").strip()
            )
            embedder = (
                ClovaQueryEmbedding()
                if _flag("LOCAL_JSON_USE_CLOVA_EMBEDDING", default=has_embedding_key)
                else None
            )
            repository = JsonStage2Repository.from_path(json_path, query_embedder=embedder)
            index = LocalJsonCorpusIndex.load(json_path)
        elif backend in {"production", "sqlite_chroma"}:
            repository = ProductionStage2Repository.from_environment()
            corpus_dir = os.getenv("CORPUS_DIR")
            if not corpus_dir:
                raise IntegrationFailure("dependency_issue", "Production Stage1 requires CORPUS_DIR")
            index = CorpusIndex.load(corpus_dir=Path(corpus_dir))
        else:
            raise IntegrationFailure("schema_issue", f"unsupported E2E_DB_BACKEND: {backend}")
        return cls(
            index=index,
            repository=repository,
            execution_mode=execution_mode,
            stage1_use_llm=_flag("STAGE1_USE_LLM"),
        )

    @staticmethod
    def _empty_stage2(status: str = "not_found", warnings: list[str] | None = None) -> dict[str, Any]:
        return {
            "query_id": str(uuid.uuid4()),
            "documents": [],
            "cited_documents": [],
            "retrieval_trace": [],
            "status": status,
            "warnings": list(warnings or []),
        }

    def run(self, *, question_id: str | None, question: str) -> E2ERun:
        resolved_question_id = str(question_id or uuid.uuid4())
        intent_object = build_intent(question, self.index, use_llm=self.stage1_use_llm)
        intent = intent_object.to_dict()
        stage1_trace = ["stage1_start", intent_object.trace_summary(), f"stage1_route={intent_object.route}"]
        stage2_result = self._empty_stage2()
        stage2_failure: IntegrationFailure | None = None

        if intent_object.route == "ok":
            try:
                model = self.model_factory()
                stage2_result = Stage2Agent(model, self.repository).run(question=question, intent=intent)
            except RetrievalError as error:
                stage2_failure = IntegrationFailure(error.classification, str(error))
            except (DependencyConfigurationError, ProviderConfigurationError) as error:
                stage2_failure = IntegrationFailure(error.classification, str(error))
            except Exception as error:  # noqa: BLE001 - integration boundary
                stage2_failure = _stage2_failure(error)

            if stage2_failure is not None:
                stage2_result = self._empty_stage2(
                    status="error",
                    warnings=[f"{stage2_failure.classification}: {stage2_failure}"],
                )
                stage2_result["retrieval_trace"] = ["stage2_failed", f"failure_class={stage2_failure.classification}"]

        stage3_result = self.stage3_service.process(
            question=question,
            stage1_intent=intent,
            stage2_result=stage2_result,
        )
        combined_trace = [*stage1_trace, *list(stage2_result.get("retrieval_trace") or [])]
        combined_warnings = [
            *list(intent.get("warnings") or []),
            *list(stage2_result.get("warnings") or []),
            *list(stage3_result.warnings),
        ]
        stage3_result = replace(stage3_result, trace=combined_trace + list(stage3_result.trace), warnings=combined_warnings)
        response = to_submission_response(resolved_question_id, question, stage3_result)
        status = (
            stage2_failure.classification
            if stage2_failure is not None
            else (intent_object.route if intent_object.route != "ok" else stage3_result.status)
        )
        return E2ERun(
            question_id=resolved_question_id,
            question=question,
            status=status,
            intent=intent,
            stage2_result=stage2_result,
            stage3_result=stage3_result,
            response=response,
        )


__all__ = ["E2ERun", "IntegrationFailure", "Stage123Application"]
