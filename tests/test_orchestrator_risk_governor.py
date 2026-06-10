import importlib
import sys
import types

sys.modules.setdefault("yfinance", types.SimpleNamespace())
sys.modules.setdefault("undetected_chromedriver", types.SimpleNamespace())

legacy = importlib.import_module("core.orchestrator.legacy")
_annotate_risk_governor = legacy._annotate_risk_governor
_build_sizing_candidates = legacy._build_sizing_candidates
_reset_orchestrator_runtime_config = legacy._reset_orchestrator_runtime_config


def _entry(
    ticker: str, *, current_price: float, entry_range: str, target: float
) -> dict:
    return {
        "ticker": ticker,
        "verdict": {
            "ticker": ticker,
            "rating": "BUY",
            "confidence": 0.75,
            "current_price": current_price,
            "entry_price_range": entry_range,
            "target_price": target,
            "stop_loss": current_price * 0.9,
            "risk_reward_ratio": 2.0,
            "expected_return": "+10.0%",
        },
    }


def test_non_deployable_top_pick_is_annotated_but_not_sized() -> None:
    top_n = [_entry("BBCA", current_price=1100, entry_range="950 - 1050", target=1200)]

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "wait_for_pullback"
    assert top_n[0]["risk_governor"]["sizing_allowed"] is False
    assert candidates == []


def test_deployable_top_pick_keeps_legacy_sizing_candidate_shape() -> None:
    top_n = [_entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1150)]

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "deployable"
    assert candidates == [
        {
            "ticker": "BBRI",
            "current_price": 1000,
            "entry_high": 1050.0,
            "stop_loss": 900.0,
            "rating": "BUY",
            "confidence": 0.75,
            "rr_ratio": 2.0,
            "target_price": 1150,
            "expected_return": "+10.0%",
        }
    ]


def test_conditional_top_pick_is_not_sized() -> None:
    top_n = [_entry("MYOR", current_price=1000, entry_range="950 - 1050", target=1150)]
    top_n[0]["verdict"].update(
        {
            "rating": "HOLD",
            "confidence": 0.41,
            "weighted_reasoning": "Counter-trend bounce below MA200.",
        }
    )

    _annotate_risk_governor(top_n)
    candidates = _build_sizing_candidates(top_n)

    assert top_n[0]["risk_governor"]["status"] == "conditional_deployable"
    assert top_n[0]["risk_governor"]["sizing_allowed"] is False
    assert candidates == []


def test_runtime_config_reset_clears_prior_defensive_overrides() -> None:
    _reset_orchestrator_runtime_config()
    keys = (
        "top_n_selection",
        "rpm_limit",
        "rr_normalization_cap",
        "min_conviction_override",
        "market_regime",
    )
    defaults = {key: legacy.ORCHESTRATOR_CONFIG[key] for key in keys}

    legacy.ORCHESTRATOR_CONFIG.update(legacy.get_regime_params("DEFENSIVE"))
    legacy.ORCHESTRATOR_CONFIG["market_regime"] = {"regime": "DEFENSIVE"}
    assert legacy.ORCHESTRATOR_CONFIG["rpm_limit"] != defaults["rpm_limit"]
    assert legacy.ORCHESTRATOR_CONFIG["min_conviction_override"] != defaults[
        "min_conviction_override"
    ]

    _reset_orchestrator_runtime_config()

    assert {key: legacy.ORCHESTRATOR_CONFIG[key] for key in keys} == defaults


def test_runtime_config_uses_codex_specific_throttle_env(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setattr(legacy.settings, "DEFAULT_LLM_PROVIDER", "codex")
        env.setenv("MAX_CONCURRENT_DEBATES", "8")
        env.setenv("GEMINI_RPM_LIMIT", "30")
        env.setenv("BATCH_DELAY_SECONDS", "0.2")
        env.setenv("CODEX_MAX_CONCURRENT_DEBATES", "3")
        env.setenv("CODEX_RPM_LIMIT", "12")
        env.setenv("CODEX_BATCH_DELAY_SECONDS", "0")

        _reset_orchestrator_runtime_config()

        assert legacy.ORCHESTRATOR_CONFIG["max_concurrent_debates"] == 3
        assert legacy.ORCHESTRATOR_CONFIG["rpm_limit"] == 12
        assert legacy.ORCHESTRATOR_CONFIG["batch_delay"] == 0

    _reset_orchestrator_runtime_config()


def test_codex_rpm_env_is_not_overwritten_by_regime(monkeypatch) -> None:
    with monkeypatch.context() as env:
        env.setattr(legacy.settings, "DEFAULT_LLM_PROVIDER", "codex")
        env.setenv("CODEX_RPM_LIMIT", "12")

        _reset_orchestrator_runtime_config()
        legacy._apply_regime_params(
            {
                "top_n_selection": 3,
                "rpm_limit": 5,
                "rr_normalization_cap": 4.0,
                "min_conviction_override": 0.7,
            }
        )

        assert legacy.ORCHESTRATOR_CONFIG["top_n_selection"] == 3
        assert legacy.ORCHESTRATOR_CONFIG["rpm_limit"] == 12
        assert legacy.ORCHESTRATOR_CONFIG["rr_normalization_cap"] == 4.0
        assert legacy.ORCHESTRATOR_CONFIG["min_conviction_override"] == 0.7

    _reset_orchestrator_runtime_config()
