from __future__ import annotations

from pathlib import Path

import pytest

import config
from integration.readiness import validate_container_settings, validate_environment

_STAGE2_ENV = (
    "STAGE2_MODE",
    "STAGE2_BACKEND",
    "STAGE2_FIXTURE_PATH",
    "STAGE2_INDEX_PATH",
    "STAGE2_CHROMA_PATH",
    "STAGE2_CHROMA_COLLECTION",
    "STAGE2_RDB_URL",
    "STAGE2_CHROMA_HOST",
    "CORPUS_DIR",
)


@pytest.fixture(autouse=True)
def _clean_stage2_env(monkeypatch):
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


def test_mode_defaults_to_fixture():
    assert config.resolve_stage2_mode() == "fixture"


def test_stage2_mode_env_is_authoritative(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "container")
    monkeypatch.setenv("STAGE2_BACKEND", "fixture")
    assert config.resolve_stage2_mode() == "container"


def test_legacy_backend_sqlite_maps_to_local_or_container(monkeypatch):
    monkeypatch.setenv("STAGE2_BACKEND", "sqlite")
    assert config.resolve_stage2_mode() == "local"
    monkeypatch.setenv("STAGE2_RDB_URL", "postgresql+psycopg://u:p@h:5432/db")
    assert config.resolve_stage2_mode() == "container"


def test_settings_resolve_paths_and_sqlite_url(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "local")
    monkeypatch.setenv("STAGE2_INDEX_PATH", "data/local_smoke/custom.db")
    settings = config.Stage2Settings.from_env()
    assert settings.mode == "local"
    assert settings.sqlite_path == config.PROJECT_ROOT / "data/local_smoke/custom.db"
    assert settings.sqlite_url == f"sqlite:///{settings.sqlite_path}"
    assert settings.chroma_collection == "stage2_chunks"


def test_validate_environment_flags_unknown_mode_and_missing_key(monkeypatch):
    assert validate_environment("local") == []
    assert any("STAGE2_MODE" in issue for issue in validate_environment("sqlite"))
    monkeypatch.delenv("CLOVA_API_KEY", raising=False)
    assert any("CLOVA_API_KEY" in issue for issue in validate_environment("fixture"))


def test_validate_container_settings_requires_both_endpoints():
    incomplete = config.Stage2Settings.from_env()
    issues = validate_container_settings(incomplete)
    assert any("STAGE2_RDB_URL" in issue for issue in issues)
    assert any("STAGE2_CHROMA_HOST" in issue for issue in issues)


def test_validate_container_settings_passes_when_both_present(monkeypatch):
    monkeypatch.setenv("STAGE2_MODE", "container")
    monkeypatch.setenv("STAGE2_RDB_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("STAGE2_CHROMA_HOST", "chroma")
    assert validate_container_settings(config.Stage2Settings.from_env()) == []
