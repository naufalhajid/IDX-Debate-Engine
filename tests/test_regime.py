"""Tests untuk core/regime.py."""

from types import SimpleNamespace

import pandas as pd
import pytest

import core.regime as regime
from core.regime import classify_regime, get_regime_params
from core.settings import Settings


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
