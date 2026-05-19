from pathlib import Path

import pytest

import core.settings as settings_module
from core.settings import ROOT_PATH, Settings


def test_settings_env_file_points_to_repo_root() -> None:
    env_file = Path(Settings.model_config["env_file"])

    assert ROOT_PATH == Path(settings_module.__file__).resolve().parents[1]
    assert env_file == ROOT_PATH / ".env"


def test_database_path_defaults_to_repo_sqlite_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_TYPE", raising=False)
    monkeypatch.delenv("DATABASE_PATH", raising=False)
    settings = Settings(_env_file=None)

    assert settings.DATABASE_TYPE == "sqlite"
    assert settings.database_path == ROOT_PATH / "db" / "idx-fundamental.db"
    assert settings.sqlite_sync_url.endswith("/db/idx-fundamental.db")
    assert settings.sqlite_async_url.endswith("/db/idx-fundamental.db")


def test_database_type_rejects_unsupported_engines() -> None:
    with pytest.raises(ValueError, match="Only SQLite is supported"):
        Settings(DATABASE_TYPE="postgresql", _env_file=None)


def test_blank_database_type_falls_back_to_sqlite() -> None:
    settings = Settings(DATABASE_TYPE="", _env_file=None)

    assert settings.DATABASE_TYPE == "sqlite"


def test_cli_log_level_normalization_handles_blank_and_invalid_values() -> None:
    from core.orchestrator.legacy import _normalize_log_level

    assert _normalize_log_level("") == "INFO"
    assert _normalize_log_level("not-a-level") == "INFO"
    assert _normalize_log_level("debug") == "DEBUG"
