"""Tests untuk core/regime.py."""

from types import SimpleNamespace

import pandas as pd
import pytest

import core.regime as regime
from core.regime import classify_regime, get_regime_params
from core.settings import Settings


def _ihsg_frame(closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"Close": closes})


def test_classify_high_volatility() -> None:
    """vol >= high_threshold harus menghasilkan regime HIGH."""
    assert classify_regime(0.025, high_threshold=0.02, low_threshold=0.01) == "HIGH"
    assert classify_regime(0.02, high_threshold=0.02, low_threshold=0.01) == "HIGH"


def test_classify_normal_volatility() -> None:
    """vol antara low dan high threshold harus menghasilkan NORMAL."""
    assert classify_regime(0.015, high_threshold=0.02, low_threshold=0.01) == "NORMAL"
    assert classify_regime(0.01, high_threshold=0.02, low_threshold=0.01) == "NORMAL"


def test_classify_low_volatility() -> None:
    """vol < low_threshold harus menghasilkan LOW."""
    assert classify_regime(0.005, high_threshold=0.02, low_threshold=0.01) == "LOW"
    assert classify_regime(0.009, high_threshold=0.02, low_threshold=0.01) == "LOW"


def test_classify_none_fallback() -> None:
    """vol=None (fetch gagal) harus fallback ke NORMAL — pipeline tidak crash."""
    assert classify_regime(None) == "NORMAL"


def test_get_regime_params_high() -> None:
    """HIGH regime harus menghasilkan parameter yang lebih konservatif."""
    params = get_regime_params("HIGH")
    assert params["top_n_selection"] < 3  # kurangi exposure
    assert params["min_conviction_override"] > 0.30  # standar lebih ketat
    assert "rpm_limit" in params


def test_get_regime_params_low() -> None:
    """LOW regime harus menghasilkan parameter yang lebih agresif."""
    params = get_regime_params("LOW")
    assert params["top_n_selection"] > 3  # opportunity lebih banyak
    assert params["min_conviction_override"] < 0.30  # lebih toleran
    assert "rpm_limit" in params


def test_get_regime_params_normal() -> None:
    """NORMAL regime harus mengembalikan dict kosong — tidak ada override."""
    params = get_regime_params("NORMAL")
    assert params == {}


def test_get_regime_params_reads_settings_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HIGH/LOW override harus berasal dari settings, bukan literal hardcode."""
    override_settings = Settings(
        REGIME_DEFENSIVE_TOP_N=6,
        REGIME_DEFENSIVE_RPM_LIMIT=4,
        REGIME_DEFENSIVE_MIN_CONVICTION=0.80,
        REGIME_DEFENSIVE_MAX_RR_FOR_SCORING=3.25,
        REGIME_HIGH_TOP_N=4,
        REGIME_HIGH_RPM_LIMIT=6,
        REGIME_HIGH_RR_CAP=3.5,
        REGIME_HIGH_MIN_CONVICTION=0.55,
        REGIME_LOW_TOP_N=8,
        REGIME_LOW_RPM_LIMIT=18,
        REGIME_LOW_RR_CAP=7.0,
        REGIME_LOW_MIN_CONVICTION=0.15,
        _env_file=None,
    )
    monkeypatch.setattr(regime, "settings", override_settings)

    assert get_regime_params("DEFENSIVE") == {
        "top_n_selection": 6,
        "rpm_limit": 4,
        "rr_normalization_cap": 3.25,
        "min_conviction_override": 0.80,
    }
    assert get_regime_params("HIGH") == {
        "top_n_selection": 4,
        "rpm_limit": 6,
        "rr_normalization_cap": 3.5,
        "min_conviction_override": 0.55,
    }
    assert get_regime_params("LOW") == {
        "top_n_selection": 8,
        "rpm_limit": 18,
        "rr_normalization_cap": 7.0,
        "min_conviction_override": 0.15,
    }


def test_compute_snapshot_weekly_drop_triggers_defensive() -> None:
    """5-day IHSG drop harus memicu DEFENSIVE dengan reason eksplisit."""
    snapshot = regime.compute_ihsg_snapshot(
        _ihsg_frame([100.0] * 215 + [94.0]),
        defensive_weekly_drop_threshold=0.05,
    )

    assert snapshot.regime == "DEFENSIVE"
    assert snapshot.defensive_triggered is True
    assert snapshot.weekly_return == pytest.approx(-0.06)
    assert "weekly_return_below_threshold" in snapshot.reasons


def test_compute_snapshot_triple_ma_breakdown_triggers_defensive_low_vol() -> None:
    """Close di bawah MA20/50/200 harus DEFENSIVE walau volatility regime LOW."""
    closes = [120.0 - (i * 0.10) for i in range(220)]
    snapshot = regime.compute_ihsg_snapshot(_ihsg_frame(closes))

    assert snapshot.regime == "DEFENSIVE"
    assert snapshot.volatility_regime == "LOW"
    assert snapshot.latest_close < snapshot.ma20
    assert snapshot.latest_close < snapshot.ma50
    assert snapshot.latest_close < snapshot.ma200
    assert "close_below_ma20_ma50_ma200" in snapshot.reasons


def test_compute_snapshot_missing_ihsg_data_falls_back_to_volatility() -> None:
    """Missing IHSG data tidak boleh crash dan harus memberi reason fallback."""
    snapshot = regime.compute_ihsg_snapshot(None)

    assert snapshot.regime == "NORMAL"
    assert snapshot.volatility_regime == "NORMAL"
    assert snapshot.defensive_triggered is False
    assert snapshot.reasons == ["ihsg_data_unavailable_fallback_to_volatility"]


async def test_fetch_ihsg_volatility_uses_buffered_period_and_exact_lookback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fetch harus download kalender ekstra, lalu hitung std dari exact lookback returns."""
    captured: dict[str, str] = {}
    closes = list(range(100, 132))

    def fake_download(*_: object, **kwargs: object) -> pd.DataFrame:
        captured["period"] = str(kwargs["period"])
        return pd.DataFrame({"Close": closes})

    monkeypatch.setattr(
        regime,
        "_get_yfinance",
        lambda: SimpleNamespace(download=fake_download),
    )

    vol = await regime.fetch_ihsg_volatility(lookback_days=20)
    expected = pd.Series(closes).pct_change().dropna().tail(20).std()

    assert captured["period"] == "30d"
    assert vol == pytest.approx(expected)


async def test_fetch_ihsg_volatility_requires_full_lookback_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Data kurang dari lookback harus fallback None, bukan dilabeli 20d vol."""

    def fake_download(*_: object, **__: object) -> pd.DataFrame:
        return pd.DataFrame({"Close": [100, 101, 102, 103, 104]})

    monkeypatch.setattr(
        regime,
        "_get_yfinance",
        lambda: SimpleNamespace(download=fake_download),
    )

    assert await regime.fetch_ihsg_volatility(lookback_days=20) is None


def test_regime_params_have_required_keys_high() -> None:
    """HIGH params harus punya semua kunci yang dibutuhkan orchestrator."""
    params = get_regime_params("HIGH")
    for key in (
        "top_n_selection",
        "rpm_limit",
        "rr_normalization_cap",
        "min_conviction_override",
    ):
        assert key in params, f"Key '{key}' missing from HIGH regime params"


def test_regime_params_have_required_keys_defensive() -> None:
    """DEFENSIVE params harus punya semua kunci yang dibutuhkan orchestrator."""
    params = get_regime_params("DEFENSIVE")
    for key in (
        "top_n_selection",
        "rpm_limit",
        "rr_normalization_cap",
        "min_conviction_override",
    ):
        assert key in params, f"Key '{key}' missing from DEFENSIVE regime params"


def test_regime_params_have_required_keys_low() -> None:
    """LOW params harus punya semua kunci yang dibutuhkan orchestrator."""
    params = get_regime_params("LOW")
    for key in (
        "top_n_selection",
        "rpm_limit",
        "rr_normalization_cap",
        "min_conviction_override",
    ):
        assert key in params, f"Key '{key}' missing from LOW regime params"
