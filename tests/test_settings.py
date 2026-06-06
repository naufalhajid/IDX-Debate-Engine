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


def test_regime_overrides_load_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REGIME_HIGH_TOP_N", "4")
    monkeypatch.setenv("REGIME_HIGH_RPM_LIMIT", "6")
    monkeypatch.setenv("REGIME_HIGH_RR_CAP", "3.5")
    monkeypatch.setenv("REGIME_HIGH_MIN_CONVICTION", "0.55")
    monkeypatch.setenv("REGIME_LOW_TOP_N", "8")
    monkeypatch.setenv("REGIME_LOW_RPM_LIMIT", "18")
    monkeypatch.setenv("REGIME_LOW_RR_CAP", "7.0")
    monkeypatch.setenv("REGIME_LOW_MIN_CONVICTION", "0.15")

    settings = Settings(_env_file=None)

    assert settings.REGIME_HIGH_TOP_N == 4
    assert settings.REGIME_HIGH_RPM_LIMIT == 6
    assert settings.REGIME_HIGH_RR_CAP == 3.5
    assert settings.REGIME_HIGH_MIN_CONVICTION == 0.55
    assert settings.REGIME_LOW_TOP_N == 8
    assert settings.REGIME_LOW_RPM_LIMIT == 18
    assert settings.REGIME_LOW_RR_CAP == 7.0
    assert settings.REGIME_LOW_MIN_CONVICTION == 0.15


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"REGIME_VOLATILITY_LOOKBACK_DAYS": 1}, "REGIME_VOLATILITY_LOOKBACK_DAYS"),
        ({"REGIME_VOLATILITY_LOW_THRESHOLD": -0.01}, "REGIME_VOLATILITY_LOW_THRESHOLD"),
        (
            {
                "REGIME_VOLATILITY_HIGH_THRESHOLD": 0.01,
                "REGIME_VOLATILITY_LOW_THRESHOLD": 0.01,
            },
            "REGIME_VOLATILITY_HIGH_THRESHOLD",
        ),
        ({"REGIME_HIGH_TOP_N": 0}, "REGIME_HIGH_TOP_N"),
        ({"REGIME_LOW_RPM_LIMIT": 0}, "REGIME_LOW_RPM_LIMIT"),
        ({"REGIME_HIGH_RR_CAP": 0}, "REGIME_HIGH_RR_CAP"),
        ({"REGIME_LOW_MIN_CONVICTION": 1.5}, "REGIME_LOW_MIN_CONVICTION"),
    ],
)
def test_regime_config_rejects_invalid_values(
    overrides: dict[str, float | int],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(_env_file=None, **overrides)


def test_cli_log_level_normalization_handles_blank_and_invalid_values() -> None:
    from core.orchestrator.legacy import _normalize_log_level

    assert _normalize_log_level("") == "INFO"
    assert _normalize_log_level("not-a-level") == "INFO"
    assert _normalize_log_level("debug") == "DEBUG"


def test_legacy_ledger_call_accepts_action_payload() -> None:
    from core.orchestrator.legacy import _ledger_call

    captured: dict[str, str] = {}

    def fake_planner_decision(**kwargs):
        captured.update(kwargs)

    _ledger_call(
        "planner decision",
        fake_planner_decision,
        action="RETRY",
        stage="DEBATE",
    )

    assert captured == {"action": "RETRY", "stage": "DEBATE"}
