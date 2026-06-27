"""Unit tests for core/regime_hmm.py — covers all 360° review fixes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from core.regime_hmm import IDXRegimeDetector


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _synthetic_prices(n: int = 500, seed: int = 7) -> pd.Series:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2022-01-03", periods=n, freq="B")
    return pd.Series(
        6000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, n))),
        index=dates,
    )


def _fitted_detector(tmp_path: Path, *, msci: bool = False) -> IDXRegimeDetector:
    prices = _synthetic_prices()
    det = IDXRegimeDetector(
        msci_review_active=msci,
        model_cache_path=str(tmp_path / "regime_test.pkl"),
    )
    det.fit(prices, force_retrain=True)
    return det


# ---------------------------------------------------------------------------
# C1 — Feature-name misalignment guard
# ---------------------------------------------------------------------------

def test_c1_name_mismatch_triggers_refit(tmp_path: Path) -> None:
    """Same-count feature rename must trigger re-fit, not silently pass bad columns to scaler."""
    prices = _synthetic_prices()
    det = _fitted_detector(tmp_path)

    original_fit_features = list(det._fit_feature_names)
    assert len(original_fit_features) > 0

    # Simulate a column rename with same count — the C1 silent bug
    det._fit_feature_names = ["__fake_col__"] + original_fit_features[1:]

    refit_calls: list[bool] = []
    original_fit = det.fit

    def spy_fit(*args, **kwargs):  # noqa: ANN001
        refit_calls.append(True)
        return original_fit(*args, **kwargs)

    det.fit = spy_fit  # type: ignore[method-assign]
    det.predict(prices)

    assert len(refit_calls) >= 1, "predict() must call fit() when feature names changed"


# ---------------------------------------------------------------------------
# W1 — Non-converged HMM seeds are skipped
# ---------------------------------------------------------------------------

def test_w1_all_seeds_non_converged_keeps_existing_model(tmp_path: Path) -> None:
    """When all seeds fail to converge, the existing model must not be replaced."""
    prices = _synthetic_prices()
    det = IDXRegimeDetector(model_cache_path=str(tmp_path / "w1.pkl"))
    det.fit(prices, force_retrain=True)
    original_model = det.model
    assert original_model is not None

    class _NeverConverges:
        def __init__(self, *_, **__):
            self.monitor_ = SimpleNamespace(converged=False)
            self.n_iter = 200

        def fit(self, X):  # noqa: ANN001, N803
            return self

        def score(self, X):  # noqa: ANN001, N803
            return 0.0

    with patch("core.regime_hmm.hmm.GaussianHMM", _NeverConverges):
        det.fit(prices, force_retrain=True)

    assert det.model is original_model, \
        "fit() must not replace model when all seeds fail to converge"


# ---------------------------------------------------------------------------
# W2a — MSCI override self-consistency
# ---------------------------------------------------------------------------

def test_w2a_msci_override_sets_all_three_fields(tmp_path: Path) -> None:
    """MSCI override must update label, state_idx, AND confidence — not just label."""
    prices = _synthetic_prices()
    det = _fitted_detector(tmp_path, msci=True)

    bear_idx     = next(k for k, v in det.state_label_map.items() if v == "BEAR_STRESS")
    sideways_idx = next(k for k, v in det.state_label_map.items() if v == "SIDEWAYS")
    bull_idx     = next(k for k, v in det.state_label_map.items() if v == "BULL")

    # SIDEWAYS wins argmax but P(BEAR_STRESS)=0.45 → must override
    fake = np.zeros((15, 3))
    fake[:, sideways_idx] = 0.50
    fake[:, bear_idx]     = 0.45
    fake[:, bull_idx]     = 0.05

    with patch.object(det.model, "predict_proba", return_value=fake):
        state = det.predict(prices)

    assert state.label == "BEAR_STRESS", f"label={state.label}"
    assert state.state_idx == bear_idx, f"state_idx={state.state_idx} != bear_idx={bear_idx}"
    assert abs(state.confidence - 0.45) < 1e-6, f"confidence={state.confidence}"
    assert state.msci_override is True


# ---------------------------------------------------------------------------
# W2b — MSCI override must NOT double-fire
# ---------------------------------------------------------------------------

def test_w2b_msci_override_does_not_fire_when_already_bear_stress(tmp_path: Path) -> None:
    """No spurious override when argmax already produces BEAR_STRESS."""
    prices = _synthetic_prices()
    det = _fitted_detector(tmp_path, msci=True)

    bear_idx     = next(k for k, v in det.state_label_map.items() if v == "BEAR_STRESS")
    sideways_idx = next(k for k, v in det.state_label_map.items() if v == "SIDEWAYS")
    bull_idx     = next(k for k, v in det.state_label_map.items() if v == "BULL")

    fake = np.zeros((15, 3))
    fake[:, bear_idx]     = 0.60
    fake[:, sideways_idx] = 0.30
    fake[:, bull_idx]     = 0.10

    with patch.object(det.model, "predict_proba", return_value=fake):
        state = det.predict(prices)

    assert state.label == "BEAR_STRESS"
    assert state.msci_override is False, "msci_override must not fire when label already BEAR_STRESS"


# ---------------------------------------------------------------------------
# W3 — Atomic write
# ---------------------------------------------------------------------------

def test_w3_atomic_write_leaves_no_tmp_files(tmp_path: Path) -> None:
    """After a successful save, no .pkl.tmp scratch files must remain."""
    cache = tmp_path / "w3.pkl"
    det = _fitted_detector(tmp_path=tmp_path)
    det.model_cache_path = cache
    det._save_model()

    assert cache.exists()
    tmp_files = list(tmp_path.glob("*.pkl.tmp"))
    assert len(tmp_files) == 0, f"Stale .tmp files: {tmp_files}"


def test_w3_pickle_round_trip_preserves_feature_names(tmp_path: Path) -> None:
    """_fit_feature_names must survive a pickle save + load cycle unchanged."""
    cache = tmp_path / "w3_rt.pkl"
    det = _fitted_detector(tmp_path=tmp_path)
    fit_names = list(det._fit_feature_names)
    assert len(fit_names) > 0

    det.model_cache_path = cache
    det._save_model()

    det2 = IDXRegimeDetector(model_cache_path=str(cache))
    assert det2._load_model() is True
    assert det2._fit_feature_names == fit_names, \
        f"fit_feature_names changed: {det2._fit_feature_names} != {fit_names}"


# ---------------------------------------------------------------------------
# N1 — _load_model() return value is logged
# ---------------------------------------------------------------------------

def test_n1_successful_cache_load_emits_info_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """_get_detector() must log INFO when _load_model() returns True."""
    import logging
    import core.regime_gate as gate_module

    gate_module._detector = None

    with patch("core.regime_gate.IDXRegimeDetector") as MockDetector:
        instance = MagicMock()
        instance._load_model.return_value = True
        MockDetector.return_value = instance

        with caplog.at_level(logging.INFO, logger="core.regime_gate"):
            gate_module._get_detector()

        instance._load_model.assert_called_once()
        info_msgs = [r.message for r in caplog.records if r.levelno == logging.INFO]
        assert any("Cached model loaded" in m for m in info_msgs), \
            f"Expected INFO log for cache load success, got: {info_msgs}"

    gate_module._detector = None  # restore singleton


# ---------------------------------------------------------------------------
# Walk-forward scaler stability
# ---------------------------------------------------------------------------

def test_scaler_unchanged_after_predict(tmp_path: Path) -> None:
    """predict() must never call scaler.fit_transform() — scaler params must be frozen."""
    prices = _synthetic_prices(n=600)
    split = int(len(prices) * 0.60)
    det = IDXRegimeDetector(model_cache_path=str(tmp_path / "wf.pkl"))
    det.fit(prices.iloc[:split], force_retrain=True)

    mean_before = det.scaler.mean_.copy()
    var_before  = det.scaler.var_.copy()

    det.predict(prices)

    assert np.max(np.abs(det.scaler.mean_ - mean_before)) < 1e-10, \
        "scaler.mean_ changed during predict() — lookahead bias"
    assert np.max(np.abs(det.scaler.var_ - var_before)) < 1e-10, \
        "scaler.var_ changed during predict() — lookahead bias"
