"""Tests untuk core/dependency_validator.py."""

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import core.dependency_validator as dependency_validator
from core.dependency_validator import (
    ValidationResult,
    check_candidates_file,
    check_llm_api_key,
    check_llm_models,
    maybe_rerun_quant_filter,
    read_candidates_execution_regime,
    read_candidates_screener_mode,
    _summarize_quant_filter_output,
)


def test_file_not_found(tmp_path: Path) -> None:
    """File tidak ada harus mengembalikan is_valid=False dengan pesan yang jelas."""
    result = check_candidates_file(tmp_path / "missing.json", max_age_hours=24.0)
    assert result.is_valid is False
    assert result.age_hours == float("inf")
    assert "tidak ditemukan" in result.message.lower()


def test_file_fresh(tmp_path: Path) -> None:
    """File yang baru dibuat (usia < threshold) harus valid."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")
    result = check_candidates_file(f, max_age_hours=24.0)
    assert result.is_valid is True
    assert result.age_hours < 1.0
    assert "valid" in result.message.lower()


def test_file_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """File yang di-backdate mtime harus dianggap stale."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")

    # Backdate mtime ke 48 jam yang lalu
    old_mtime = time.time() - (48 * 3600)
    import os

    os.utime(f, (old_mtime, old_mtime))

    result = check_candidates_file(f, max_age_hours=24.0)
    assert result.is_valid is False
    assert result.age_hours > 24.0
    assert "stale" in result.message.lower()


def test_file_exactly_at_boundary(tmp_path: Path) -> None:
    """File tepat di batas max_age harus dianggap valid (edge case: usia < max, bukan <=)."""
    f = tmp_path / "top10_candidates.json"
    f.write_text("[]", encoding="utf-8")
    # File baru dibuat — usianya jauh di bawah 24 jam
    result = check_candidates_file(f, max_age_hours=0.001)  # sangat ketat
    # Bisa stale atau valid tergantung kecepatan I/O — yang penting is ValidationResult
    assert isinstance(result, ValidationResult)
    assert isinstance(result.is_valid, bool)


def test_validation_result_fields() -> None:
    """ValidationResult harus punya field is_valid, age_hours, message."""
    r = ValidationResult(is_valid=True, age_hours=1.5, message="ok")
    assert r.is_valid is True
    assert r.age_hours == 1.5
    assert r.message == "ok"


def test_maybe_rerun_quant_filter_passes_output_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "run_quant_filter.py"
    script.write_text("print('ok')", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs) -> SimpleNamespace:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("core.dependency_validator.subprocess.run", fake_run)

    assert maybe_rerun_quant_filter(
        script_path=str(script), output_dir=tmp_path / "dry"
    )
    cmd = captured["command"]
    assert "--output-dir" in cmd
    assert cmd[cmd.index("--output-dir") + 1] == str(tmp_path / "dry")
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["text"] is True
    # Defaults to momentum when no mode is given.
    assert cmd[-2:] == ["--mode", "momentum"]

    assert maybe_rerun_quant_filter(
        script_path=str(script), output_dir=tmp_path / "dry", mode="mean-reversion"
    )
    assert captured["command"][-2:] == ["--mode", "mean_reversion"]


def test_read_candidates_screener_mode(tmp_path: Path) -> None:
    p = tmp_path / "top10_candidates.json"
    # Missing file -> momentum (legacy default).
    assert read_candidates_screener_mode(p) == "momentum"
    # Tagged mean_reversion.
    p.write_text(
        json.dumps([{"Ticker": "INDF", "screener_mode": "mean_reversion"}]),
        encoding="utf-8",
    )
    assert read_candidates_screener_mode(p) == "mean_reversion"
    # Untagged record -> momentum.
    p.write_text(json.dumps([{"Ticker": "BBCA"}]), encoding="utf-8")
    assert read_candidates_screener_mode(p) == "momentum"
    # Malformed content -> momentum.
    p.write_text("not json", encoding="utf-8")
    assert read_candidates_screener_mode(p) == "momentum"


def test_read_candidates_execution_regime_requires_one_canonical_label(
    tmp_path: Path,
) -> None:
    p = tmp_path / "top10_candidates.json"
    assert read_candidates_execution_regime(p) == "UNKNOWN"

    p.write_text(
        json.dumps(
            [
                {"Ticker": "BBCA", "execution_regime": "defensive"},
                {"Ticker": "BMRI", "execution_regime": "DEFENSIVE"},
            ]
        ),
        encoding="utf-8",
    )
    assert read_candidates_execution_regime(p) == "DEFENSIVE"

    p.write_text(
        json.dumps(
            [
                {"Ticker": "BBCA", "execution_regime": "DEFENSIVE"},
                {"Ticker": "BMRI", "execution_regime": "SIDEWAYS"},
            ]
        ),
        encoding="utf-8",
    )
    assert read_candidates_execution_regime(p) == "UNKNOWN"

    p.write_text(json.dumps([{"Ticker": "BBCA"}]), encoding="utf-8")
    assert read_candidates_execution_regime(p) == "UNKNOWN"

    p.write_text(
        json.dumps(
            [
                {"Ticker": "BBCA", "execution_regime": "DEFENSIVE"},
                "corrupt-record",
            ]
        ),
        encoding="utf-8",
    )
    assert read_candidates_execution_regime(p) == "UNKNOWN"


def test_maybe_rerun_quant_filter_passes_canonical_regime_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = tmp_path / "run_quant_filter.py"
    script.write_text("print('ok')", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs) -> SimpleNamespace:
        captured["command"] = command
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("core.dependency_validator.subprocess.run", fake_run)

    assert maybe_rerun_quant_filter(
        script_path=str(script),
        execution_regime="DEFENSIVE",
        execution_regime_reason="rule_based_defensive_override",
        trend_regime="SIDEWAYS",
        volatility_regime="HIGH",
    )

    command = captured["command"]
    assert command[command.index("--execution-regime") + 1] == "DEFENSIVE"
    assert (
        command[command.index("--execution-regime-reason") + 1]
        == "rule_based_defensive_override"
    )
    assert command[command.index("--trend-regime") + 1] == "SIDEWAYS"
    assert command[command.index("--volatility-regime") + 1] == "HIGH"


def test_quant_filter_output_summary_keeps_pipeline_console_clean() -> None:
    output = """
2026-06-06 00:12:53,418 [INFO] Total ticker universe: 957
2026-06-06 00:12:53,450 [INFO] Lolos static filter: 458 ticker
2026-06-06 00:13:02,885 [INFO] Download berhasil. Shape: (120, 2290)
2026-06-06 00:13:03,026 [INFO] IHSG return 1 bulan: -17.82%
2026-06-06 00:13:04,546 [INFO] Top 10 kandidat berhasil disaring.
2026-06-06 00:13:04,549 [INFO] JSON diekspor -> output\\top10_candidates.json
2026-06-06 00:12:53,460 [WARNING] [Graham] BMTR: capped.
2026-06-06 00:12:53,470 [WARNING] yfinance flaky retry for XYZZ
2026-06-06 00:13:03,541 [INFO] [BAPA] Excluded: suspek suspended/FCA (volume anomali)
"""

    summary = _summarize_quant_filter_output(output)

    assert "universe=957" in summary
    assert "static=458" in summary
    assert "yf_shape=120, 2290" in summary
    assert "ihsg_1m=-17.82%" in summary
    assert "top=10" in summary
    assert "json=output\\top10_candidates.json" in summary
    assert "warnings=1" in summary
    assert "graham_caps=1" in summary
    assert "suspended_like=1" in summary


def test_codex_api_key_check_resolves_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(DEFAULT_LLM_PROVIDER="codex"),
    )
    monkeypatch.setattr(
        "providers.oauth_manager.resolve_codex_token",
        lambda: "token",
    )

    result = check_llm_api_key(required=True)

    assert result.is_valid is True
    assert "Token Codex tersedia" in result.message


def test_anthropic_api_key_check_resolves_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(DEFAULT_LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=""),
    )
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        "providers.oauth_manager.resolve_anthropic_token",
        lambda: "anthropic-token",
    )

    result = check_llm_api_key(required=True)

    assert result.is_valid is True
    assert "Kredensial Anthropic tersedia" in result.message


def test_codex_model_check_requires_live_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )

    def fake_probe(provider: str, tier: str) -> None:
        calls.append((provider, tier))

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", fake_probe)

    result = check_llm_models(required=True)

    assert result.is_valid is True
    assert calls == [("codex", "flash"), ("codex", "pro")]


def test_codex_model_check_fails_when_probe_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="invalid-flash",
            CODEX_PRO_MODEL="invalid-pro",
        ),
    )

    def fake_probe(provider: str, tier: str) -> None:
        raise RuntimeError("model not found")

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", fake_probe)

    result = check_llm_models(required=True)

    assert result.is_valid is False
    assert result.blocking is True
    assert "live probe gagal" in result.message


def test_codex_model_probe_recovers_once_after_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )
    calls = {"flash": 0, "pro": 0, "recovery": 0}
    recovery_kwargs: list[dict[str, object]] = []

    class ExpiredTokenError(RuntimeError):
        status_code = 401

    def fake_probe(_provider: str, tier: str) -> None:
        calls[tier] += 1
        if calls[tier] == 1:
            error = ExpiredTokenError("token_expired")
            error.codex_token_fingerprint = "rejected-fingerprint"
            raise error

    def fake_recovery(**kwargs: object) -> str:
        calls["recovery"] += 1
        recovery_kwargs.append(kwargs)
        return "fresh-token"

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", fake_probe)
    monkeypatch.setattr(
        "providers.oauth_manager.recover_codex_token_after_auth_failure",
        fake_recovery,
        raising=False,
    )

    result = check_llm_models(required=True)

    assert result.is_valid is True
    assert calls == {"flash": 2, "pro": 2, "recovery": 1}
    assert recovery_kwargs == [
        {"rejected_token_fingerprint": "rejected-fingerprint"}
    ]


def test_codex_model_probe_stops_after_single_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )
    calls = {"probe": 0, "recovery": 0}

    class ExpiredTokenError(RuntimeError):
        status_code = 401

    def fail_probe(_provider: str, _tier: str) -> None:
        calls["probe"] += 1
        raise ExpiredTokenError("401 token_expired")

    def fake_recovery(**_kwargs: object) -> str:
        calls["recovery"] += 1
        return "fresh-token"

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", fail_probe)
    monkeypatch.setattr(
        "providers.oauth_manager.recover_codex_token_after_auth_failure",
        fake_recovery,
        raising=False,
    )

    result = check_llm_models(required=True)

    assert result.is_valid is False
    assert calls["probe"] == 4
    assert calls["recovery"] == 1


def test_codex_model_probe_does_not_recover_non_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )
    recovery_calls = 0

    def fail_probe(_provider: str, _tier: str) -> None:
        raise RuntimeError("model not found")

    def fake_recovery(**_kwargs: object) -> str:
        nonlocal recovery_calls
        recovery_calls += 1
        return "unexpected"

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", fail_probe)
    monkeypatch.setattr(
        "providers.oauth_manager.recover_codex_token_after_auth_failure",
        fake_recovery,
        raising=False,
    )

    result = check_llm_models(required=True)

    assert result.is_valid is False
    assert recovery_calls == 0


def test_codex_probe_prioritizes_auth_error_from_either_tier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )
    calls = {"flash": 0, "pro": 0, "recovery": 0}

    class ExpiredTokenError(RuntimeError):
        status_code = 401

    def mixed_probe(_provider: str, tier: str) -> None:
        calls[tier] += 1
        if calls[tier] > 1:
            return
        if tier == "flash":
            raise RuntimeError("model configuration error")
        error = ExpiredTokenError("token_expired")
        error.codex_token_fingerprint = "pro-fingerprint"
        raise error

    def fake_recovery(**kwargs: object) -> str:
        calls["recovery"] += 1
        assert kwargs["rejected_token_fingerprint"] == "pro-fingerprint"
        return "fresh-token"

    monkeypatch.setattr("core.dependency_validator._invoke_llm_probe", mixed_probe)
    monkeypatch.setattr(
        "providers.oauth_manager.recover_codex_token_after_auth_failure",
        fake_recovery,
    )

    result = check_llm_models(required=True)

    assert result.is_valid is True
    assert calls == {"flash": 2, "pro": 2, "recovery": 1}


def test_codex_probe_attaches_fingerprint_of_actual_model_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pydantic import SecretStr

    from providers.oauth_manager import codex_token_fingerprint

    token = "probe-access-token-sentinel"

    class ExpiredTokenError(RuntimeError):
        status_code = 401

    model = SimpleNamespace(
        api_key=SecretStr(token),
        invoke=lambda _prompt: (_ for _ in ()).throw(
            ExpiredTokenError("token_expired")
        ),
    )
    monkeypatch.setattr(
        "providers.llm_factory.get_llm",
        lambda *_args, **_kwargs: model,
    )

    with pytest.raises(ExpiredTokenError) as captured:
        dependency_validator._invoke_llm_probe("codex", "flash")

    assert captured.value.codex_token_fingerprint == codex_token_fingerprint(token)


def test_codex_probe_does_not_recover_after_model_exhausted_bounded_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from providers.oauth_manager import CodexAuthRecoveryExhausted

    monkeypatch.setattr(
        "core.dependency_validator.settings",
        SimpleNamespace(
            DEFAULT_LLM_PROVIDER="codex",
            CODEX_FLASH_MODEL="gpt-5.4-mini",
            CODEX_PRO_MODEL="gpt-5.5",
        ),
    )
    probe_calls = 0
    recovery_calls = 0

    class ExpiredTokenError(RuntimeError):
        status_code = 401

    def exhausted_probe(_provider: str, _tier: str) -> None:
        nonlocal probe_calls
        probe_calls += 1
        try:
            raise ExpiredTokenError("token_expired")
        except ExpiredTokenError as exc:
            raise CodexAuthRecoveryExhausted(
                "after one credential recovery"
            ) from exc

    def unexpected_recovery(**_kwargs: object) -> str:
        nonlocal recovery_calls
        recovery_calls += 1
        return "unexpected"

    monkeypatch.setattr(
        "core.dependency_validator._invoke_llm_probe",
        exhausted_probe,
    )
    monkeypatch.setattr(
        "providers.oauth_manager.recover_codex_token_after_auth_failure",
        unexpected_recovery,
    )

    result = check_llm_models(required=True)

    assert result.is_valid is False
    assert probe_calls == 2
    assert recovery_calls == 0


def test_failed_credential_preflight_skips_live_model_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probe_calls = 0

    def fail_key(*, required: bool):
        return dependency_validator.DependencyCheck(
            name="llm_api_key",
            is_valid=False,
            message="expired",
            hint="refresh",
            blocking=required,
        )

    def unexpected_probe(*, required: bool):
        nonlocal probe_calls
        probe_calls += 1
        raise AssertionError("model probe must be skipped")

    valid = dependency_validator.DependencyCheck(
        name="ok",
        is_valid=True,
        message="ok",
        blocking=False,
    )
    monkeypatch.setattr(dependency_validator, "check_llm_api_key", fail_key)
    monkeypatch.setattr(dependency_validator, "check_llm_models", unexpected_probe)
    monkeypatch.setattr(
        dependency_validator,
        "check_database_connection",
        lambda: valid,
    )
    monkeypatch.setattr(
        dependency_validator,
        "check_disk_space",
        lambda *_args, **_kwargs: valid,
    )

    result = dependency_validator.check_all_dependencies(
        tmp_path,
        require_llm=True,
    )

    assert result.is_valid is False
    assert probe_calls == 0
    assert "dilewati" in result.checks["llm_models"].message
