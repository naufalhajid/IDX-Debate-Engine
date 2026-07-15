import importlib
import sys
import types
from unittest.mock import patch

sys.modules.setdefault("yfinance", types.SimpleNamespace())
sys.modules.setdefault("undetected_chromedriver", types.SimpleNamespace())

legacy = importlib.import_module("core.orchestrator.legacy")
_annotate_risk_governor = legacy._annotate_risk_governor
_apply_circuit_breaker = legacy._apply_circuit_breaker
_build_sizing_candidates = legacy._build_sizing_candidates
_reset_orchestrator_runtime_config = legacy._reset_orchestrator_runtime_config
_risk_holds = legacy._risk_holds


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
    # target=1350: (1350-1050)/(1050-900)=300/150=2.0x canonical R/R.
    top_n = [_entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1350)]
    top_n[0]["verdict"].update({"risk_overvalued": False, "is_overvalued": False})

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
            "target_price": 1350,
            "expected_return": "+10.0%",
        }
    ]


def test_conditional_top_pick_is_not_sized() -> None:
    # After P2: low_confidence is a hard reject — HOLD + confidence=0.41 → reject, not conditional.
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

    assert top_n[0]["risk_governor"]["status"] == "reject"
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


def test_annotate_risk_governor_exception_blocks_sizing() -> None:
    """annotate_risk() exception → fail-closed risk_governor with sizing_allowed=False."""
    entry = _entry("BBCA", current_price=1000, entry_range="950 - 1050", target=1150)

    with patch("core.orchestrator.legacy.annotate_risk", side_effect=RuntimeError("mock failure")):
        _annotate_risk_governor([entry])

    rg = entry.get("risk_governor")
    assert isinstance(rg, dict), "risk_governor must be set even when annotate_risk raises"
    assert rg["sizing_allowed"] is False
    assert "governor_error" in rg["reason_codes"]


def test_risk_holds_entry_without_governor_is_fail_closed() -> None:
    """Entry missing risk_governor must appear in holds — fail-closed after P0 fix."""
    entry = {"ticker": "BBRI"}
    holds = _risk_holds([entry])

    assert len(holds) == 1
    assert holds[0]["ticker"] == "BBRI"


def test_risk_holds_sizing_allowed_true_is_excluded() -> None:
    """Entry with sizing_allowed=True must not appear in holds."""
    entry = {
        "ticker": "BBCA",
        "risk_governor": {
            "ticker": "BBCA",
            "status": "deployable",
            "sizing_allowed": True,
            "message": "ok",
        },
    }
    holds = _risk_holds([entry])

    assert holds == []


def test_circuit_breaker_blocks_all_entries_when_daily_loss_exceeds_threshold() -> None:
    # P5: realized_loss_pct = -4% (> 3% threshold) → all entries blocked.
    top_n = [
        _entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1290),
        _entry("BBCA", current_price=8000, entry_range="7800 - 8200", target=9000),
    ]
    fired = _apply_circuit_breaker(top_n, {"realized_loss_pct": -0.04})

    assert fired is True
    for entry in top_n:
        rg = entry.get("risk_governor", {})
        assert rg["sizing_allowed"] is False
        assert "circuit_breaker" in rg["reason_codes"]
        assert rg["status"] == "reject"


def test_circuit_breaker_does_not_fire_when_loss_below_threshold() -> None:
    # P5: realized_loss_pct = -2% (< 3%) → breaker does not fire.
    top_n = [_entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1290)]
    fired = _apply_circuit_breaker(top_n, {"realized_loss_pct": -0.02})

    assert fired is False
    assert "risk_governor" not in top_n[0]


def test_circuit_breaker_does_not_fire_on_empty_portfolio_state() -> None:
    # No portfolio_state → breaker never fires.
    top_n = [_entry("TLKM", current_price=3000, entry_range="2900 - 3100", target=3500)]
    fired = _apply_circuit_breaker(top_n, {})

    assert fired is False


def test_circuit_breaker_amount_path_fires_when_loss_exceeds_capital_threshold() -> None:
    # P5: IDR-amount path — loss 40M on capital 1B = -4% > 3% threshold.
    top_n = [_entry("BBCA", current_price=8000, entry_range="7800 - 8200", target=9000)]
    fired = _apply_circuit_breaker(
        top_n,
        {"realized_loss_amount": -40_000_000, "total_capital": 1_000_000_000},
    )

    assert fired is True
    assert top_n[0]["risk_governor"]["sizing_allowed"] is False


def test_check_circuit_breaker_standalone() -> None:
    from core.risk_governor import check_circuit_breaker, CIRCUIT_BREAKER_DAILY_LOSS_PCT

    assert CIRCUIT_BREAKER_DAILY_LOSS_PCT == 0.03
    assert check_circuit_breaker({"realized_loss_pct": -0.04}) is True
    assert check_circuit_breaker({"realized_loss_pct": -0.03}) is True
    assert check_circuit_breaker({"realized_loss_pct": -0.02}) is False
    assert check_circuit_breaker({"realized_loss_pct": 0.05}) is False  # profit
    assert check_circuit_breaker({}) is False


def test_circuit_breaker_fires_then_build_sizing_candidates_is_empty() -> None:
    """End-to-end: breaker fires → _build_sizing_candidates returns empty list."""
    top_n = [
        _entry("BBRI", current_price=1000, entry_range="950 - 1050", target=1290),
        _entry("BBCA", current_price=8000, entry_range="7800 - 8200", target=9000),
    ]
    fired = _apply_circuit_breaker(top_n, {"realized_loss_pct": -0.04})
    candidates = _build_sizing_candidates(top_n)

    assert fired is True
    assert candidates == []
