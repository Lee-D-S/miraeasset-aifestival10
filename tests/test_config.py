from __future__ import annotations

from pathlib import Path

import pytest

import config
from integration.readiness import validate_environment

_STAGE2_ENV = (
    "STAGE2_MODE",
    "STAGE2_INDEX_PATH",
    "STAGE2_CHROMA_PATH",
    "STAGE2_CHROMA_COLLECTION",
    "STAGE2_SQL_TABLE",
    "STAGE2_EMBEDDING",
    "STAGE2_ALLOW_PARTIAL_INDEX",
    "CLOVA_LLM_ENABLED",
    "STAGE1_USE_LLM",
    "QUERY_PLANNER_LLM_ENABLED",
    "CLOVA_RERANKER_ENABLED",
    "CORPUS_DIR",
)


@pytest.fixture(autouse=True)
def _clean_retriever_env(monkeypatch):
    for name in _STAGE2_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CLOVA_API_KEY", "test-key")


def test_resolve_path_anchors_relative_values_to_project_root():
    assert config.resolve_path("data/x.db", Path("/def")) == config.PROJECT_ROOT / "data/x.db"


def test_resolve_path_keeps_absolute_and_falls_back_to_default(tmp_path):
    absolute = tmp_path / "here.db"
    assert config.resolve_path(str(absolute), Path("/def")) == absolute
    assert config.resolve_path("", tmp_path / "default.db") == tmp_path / "default.db"
    assert config.resolve_path(None, tmp_path / "default.db") == tmp_path / "default.db"


def test_mode_defaults_to_local():
    assert config.resolve_retriever_mode() == "local"


def test_retriever_mode_container_is_rejected(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "container")
    assert config.resolve_retriever_mode() == "container"
    assert any("STAGE2_MODE" in issue for issue in validate_environment("container"))


def test_settings_resolve_paths_and_sqlite_url(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "local")
    monkeypatch.setenv("STAGE2_INDEX_PATH", "data/test_index/custom.db")
    settings = config.RetrieverSettings.from_env()
    assert settings.mode == "local"
    assert settings.sqlite_path == config.PROJECT_ROOT / "data/test_index/custom.db"
    assert settings.sqlite_url == f"sqlite:///{settings.sqlite_path}"
    assert settings.chroma_collection == "chunk_vectors"
    assert settings.sqlite_table == "chunk_index"
    assert settings.embedding == config.DEFAULT_STAGE2_EMBEDDING


def test_validate_environment_flags_unknown_mode(monkeypatch):
    assert validate_environment("local") == []
    assert any("STAGE2_MODE" in issue for issue in validate_environment("sqlite"))


def test_validate_environment_requires_key_only_for_live_llm(monkeypatch):
    monkeypatch.delenv("CLOVA_API_KEY", raising=False)
    assert validate_environment("local") == []
    monkeypatch.setenv("CLOVA_LLM_ENABLED", "true")
    assert any("CLOVA_API_KEY" in issue for issue in validate_environment("local"))


@pytest.mark.parametrize("flag", ["STAGE1_USE_LLM", "QUERY_PLANNER_LLM_ENABLED", "CLOVA_RERANKER_ENABLED"])
def test_validate_environment_requires_key_for_each_clova_capability(monkeypatch, flag):
    monkeypatch.delenv("CLOVA_API_KEY", raising=False)
    monkeypatch.setenv(flag, "1" if flag in {"STAGE1_USE_LLM", "QUERY_PLANNER_LLM_ENABLED"} else "true")
    assert any("CLOVA_API_KEY" in issue for issue in validate_environment("local"))


def test_validate_environment_rejects_unknown_embedding_and_table(monkeypatch):
    monkeypatch.setenv("STAGE2_EMBEDDING", "unknown")
    monkeypatch.setenv("STAGE2_SQL_TABLE", "unknown")
    issues = validate_environment("local")
    assert any("STAGE2_EMBEDDING" in issue for issue in issues)
    assert any("STAGE2_SQL_TABLE" in issue for issue in issues)


# Only the supplied-index embedding alias is valid; model names and legacy
# instruct aliases must be rejected at the configuration boundary.
@pytest.mark.parametrize("embedding", ["clova", "multilingual-e5-large", "e5-instruct"])
def test_validate_environment_rejects_legacy_embedding_aliases(monkeypatch, embedding):
    monkeypatch.setenv("STAGE2_EMBEDDING", embedding)
    assert any("STAGE2_EMBEDDING" in issue for issue in validate_environment("local"))


@pytest.mark.parametrize("embedding", ["e5"])
def test_validate_environment_accepts_supported_embeddings(monkeypatch, embedding):
    monkeypatch.setenv("STAGE2_EMBEDDING", embedding)
    assert not any(
        "STAGE2_EMBEDDING" in issue for issue in validate_environment("local")
    )


def test_validate_environment_rejects_fixture_mode(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "fixture")
    assert any("STAGE2_MODE" in issue for issue in validate_environment("fixture"))
