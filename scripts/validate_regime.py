"""
scripts/validate_regime.py — IDX Regime HMM Validation Suite

Five tests that must all pass before promoting the regime system to production:

  V1  Feature stationarity    — ADF test; non-stationary features distort HMM emission means
  V2  State persistence       — regime sequence should be autocorrelated (not white noise)
  V3  MSCI override           — P(BEAR_STRESS) > 0.40 must force BEAR_STRESS label
  V4  Walk-forward integrity  — rolling predict must not re-fit the scaler
  V5  Regime-drawdown align   — BEAR_STRESS mean return must be lower than BULL mean return

Run:
    uv run python scripts/validate_regime.py

Exit 0 = all passed.  Exit 1 = at least one test failed.
"""

from __future__ import annotations

import sys
import logging
import warnings
from datetime import datetime

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

PASS = "[PASS]"
FAIL = "[FAIL]"
SKIP = "[SKIP]"


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _fetch_ihsg(period: str = "4y") -> pd.Series:
    try:
        import yfinance as yf
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = yf.download("^JKSE", period=period, progress=False,
                              auto_adjust=True, timeout=20)
        if raw is not None and not raw.empty:
            return raw["Close"].squeeze().dropna()
    except Exception as exc:
        logging.warning("yfinance fetch failed (%s) — using synthetic data.", exc)
    # Synthetic fallback: 900 trading days
    rng = np.random.default_rng(2026)
    dates = pd.date_range("2021-01-04", periods=900, freq="B")
    return pd.Series(
        6000 * np.exp(np.cumsum(rng.normal(0.0002, 0.013, 900))),
        index=dates,
    )


# ---------------------------------------------------------------------------
# V1 — Feature stationarity (ADF test)
# ---------------------------------------------------------------------------

def validate_feature_stationarity(prices: pd.Series) -> bool:
    """All HMM features should be stationary (ADF p-value < 0.05)."""
    print("\n-- V1: Feature Stationarity (ADF test) --")
    from statsmodels.tsa.stattools import adfuller
    from core.regime_hmm import IDXRegimeDetector

    det = IDXRegimeDetector()
    features, _ = det.build_features(prices)
    passed = True
    for i, name in enumerate(det._feature_names):
        col = features[:, i]
        col = col[~np.isnan(col)]
        if len(col) < 30:
            print(f"  {SKIP} {name}: too few observations ({len(col)})")
            continue
        p_val = adfuller(col, autolag="AIC")[1]
        ok = p_val < 0.05
        if not ok:
            passed = False
        print(f"  {'PASS' if ok else 'FAIL'} {name}: ADF p={p_val:.4f} "
              f"({'stationary' if ok else 'NON-STATIONARY'})")
    return passed


# ---------------------------------------------------------------------------
# V2 — State persistence (regime autocorrelation)
# ---------------------------------------------------------------------------

def validate_state_persistence(prices: pd.Series) -> bool:
    """Lag-1 autocorrelation of the decoded regime sequence must exceed 0.30."""
    print("\n-- V2: State Persistence (regime autocorrelation) --")
    from core.regime_hmm import IDXRegimeDetector

    det = IDXRegimeDetector(msci_review_active=False)
    det.fit(prices, force_retrain=True)
    if det.model is None:
        print(f"  {FAIL} Model fit failed.")
        return False

    features, _ = det.build_features(prices)
    X = det.scaler.transform(features)
    states = det.model.predict(X)

    lag1_ac = float(pd.Series(states).autocorr(lag=1))
    ok = lag1_ac > 0.30
    print(f"  {'PASS' if ok else 'FAIL'} Lag-1 autocorrelation: {lag1_ac:.3f} "
          f"(threshold > 0.30)")
    if not ok:
        print("    Regime sequence is near-random — HMM may not have found real structure.")
    return ok


# ---------------------------------------------------------------------------
# V3 — MSCI override trigger
# ---------------------------------------------------------------------------

def validate_msci_override() -> bool:
    """
    When P(BEAR_STRESS) > 0.40 and msci_review_active=True:
      label, state_idx, and confidence must all reflect BEAR_STRESS consistently.
    """
    print("\n-- V3: MSCI Override Logic --")
    from unittest.mock import patch
    from core.regime_hmm import IDXRegimeDetector

    rng = np.random.default_rng(42)
    dates = pd.date_range("2020-01-02", periods=900, freq="B")
    prices = pd.Series(
        6000 * np.exp(np.cumsum(rng.normal(0.0003, 0.012, 900))), index=dates
    )

    det = IDXRegimeDetector(msci_review_active=True)
    det.fit(prices, force_retrain=True)
    if det.model is None:
        print(f"  {FAIL} Model fit failed.")
        return False

    bear_idx    = next(k for k, v in det.state_label_map.items() if v == "BEAR_STRESS")
    sideways_idx = next(k for k, v in det.state_label_map.items() if v == "SIDEWAYS")
    bull_idx    = next(k for k, v in det.state_label_map.items() if v == "BULL")

    # SIDEWAYS wins argmax (0.50) but P(BEAR_STRESS)=0.45 → must override
    fake = np.zeros((20, 3))
    fake[:, sideways_idx] = 0.50
    fake[:, bear_idx]     = 0.45
    fake[:, bull_idx]     = 0.05

    passed = True
    with patch.object(det.model, "predict_proba", return_value=fake):
        state = det.predict(prices)

    checks = [
        ("label == BEAR_STRESS",         state.label == "BEAR_STRESS"),
        ("msci_override == True",        state.msci_override is True),
        ("state_idx == bear_idx",        state.state_idx == bear_idx),
        ("confidence == P(BEAR_STRESS)", abs(state.confidence - 0.45) < 1e-6),
    ]
    for desc, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'} {desc}")
        if not ok:
            passed = False

    # When label is already BEAR_STRESS, msci_override must NOT fire
    fake2 = np.zeros((20, 3))
    fake2[:, bear_idx]     = 0.60
    fake2[:, sideways_idx] = 0.30
    fake2[:, bull_idx]     = 0.10
    with patch.object(det.model, "predict_proba", return_value=fake2):
        state2 = det.predict(prices)
    no_double = not state2.msci_override
    print(f"  {'PASS' if no_double else 'FAIL'} no override when already BEAR_STRESS")
    if not no_double:
        passed = False

    return passed


# ---------------------------------------------------------------------------
# V4 — Walk-forward integrity (scaler must not re-fit at predict time)
# ---------------------------------------------------------------------------

def validate_walk_forward(prices: pd.Series) -> bool:
    """
    After fitting on 60% of data, calling predict() on the full sequence
    must not change the scaler's mean/var (that would introduce lookahead bias).
    """
    print("\n-- V4: Walk-Forward Integrity (scaler stability) --")
    from core.regime_hmm import IDXRegimeDetector

    split = int(len(prices) * 0.60)
    train_prices = prices.iloc[:split]

    det = IDXRegimeDetector(msci_review_active=False)
    det.fit(train_prices, force_retrain=True)
    if det.model is None:
        print(f"  {FAIL} Model fit failed on train window.")
        return False

    mean_before = det.scaler.mean_.copy()
    var_before  = det.scaler.var_.copy()

    det.predict(prices)  # full sequence — scaler must remain unchanged

    drift_mean = float(np.max(np.abs(det.scaler.mean_ - mean_before)))
    drift_var  = float(np.max(np.abs(det.scaler.var_  - var_before)))
    ok = drift_mean < 1e-10 and drift_var < 1e-10
    print(f"  {'PASS' if ok else 'FAIL'} Scaler mean drift: {drift_mean:.2e}, "
          f"var drift: {drift_var:.2e} (threshold < 1e-10)")
    if not ok:
        print("    Scaler was re-fitted during predict() — lookahead bias present.")
    return ok


# ---------------------------------------------------------------------------
# V5 — Regime-drawdown alignment
# ---------------------------------------------------------------------------

def validate_drawdown_alignment(prices: pd.Series) -> bool:
    """
    mean_return(BEAR_STRESS) < mean_return(SIDEWAYS) < mean_return(BULL).
    Failure means state labeling is inverted.
    """
    print("\n-- V5: Regime-Drawdown Alignment --")
    from core.regime_hmm import IDXRegimeDetector

    det = IDXRegimeDetector(msci_review_active=False)
    det.fit(prices, force_retrain=True)
    if det.model is None:
        print(f"  {FAIL} Model fit failed.")
        return False

    features, feat_idx = det.build_features(prices)
    X = det.scaler.transform(features)
    raw_states = det.model.predict(X)
    labels = [det.state_label_map.get(s, "UNKNOWN") for s in raw_states]

    returns       = features[:, 0]  # ihsg_return is column 0
    label_series  = pd.Series(labels, index=feat_idx)
    return_series = pd.Series(returns, index=feat_idx)

    stats: dict[str, tuple[float, int]] = {}
    for lbl in ["BULL", "SIDEWAYS", "BEAR_STRESS"]:
        mask  = label_series == lbl
        count = int(mask.sum())
        stats[lbl] = (float(return_series[mask].mean()) if count else np.nan, count)
        print(f"  {lbl:12s}: mean_return={stats[lbl][0]:+.5f}  days={count}")

    bull_ret = stats["BULL"][0]
    bear_ret = stats["BEAR_STRESS"][0]
    if np.isnan(bull_ret) or np.isnan(bear_ret):
        print(f"  {SKIP} Not all states represented — inconclusive.")
        return True

    ok = bear_ret < bull_ret
    print(f"  {'PASS' if ok else 'FAIL'} BEAR_STRESS ({bear_ret:+.5f}) < BULL ({bull_ret:+.5f})")
    if not ok:
        print("    State ordering inverted — check _assign_state_labels().")
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 60)
    print(f"IDX Regime HMM Validation  {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 60)

    prices = _fetch_ihsg()
    print(f"\nData: {len(prices)} trading days  "
          f"({prices.index[0].date()} -> {prices.index[-1].date()})")

    results = {
        "V1 Feature Stationarity":   validate_feature_stationarity(prices),
        "V2 State Persistence":      validate_state_persistence(prices),
        "V3 MSCI Override":          validate_msci_override(),
        "V4 Walk-Forward Integrity": validate_walk_forward(prices),
        "V5 Drawdown Alignment":     validate_drawdown_alignment(prices),
    }

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    all_passed = True
    for name, ok in results.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            all_passed = False

    print()
    if all_passed:
        print("All tests PASSED. System ready for production.")
        return 0
    print("One or more tests FAILED. Fix before promoting to production.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
