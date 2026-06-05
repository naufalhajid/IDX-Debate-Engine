"""Tests untuk core/regime.py."""

from core.regime import classify_regime, get_regime_params


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
