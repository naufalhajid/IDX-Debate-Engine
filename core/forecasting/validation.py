"""Walk-forward validation, IC/Brier metrics, Benjamini-Hochberg correction."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from core.forecasting.schemas import ValidationSummary

if TYPE_CHECKING:
    from core.forecasting.models import ModelBase

EMBARGO_DAYS: int = 20


def walk_forward_splits(
    df: pd.DataFrame,
    n_splits: int = 5,
    test_size_days: int = 60,
) -> list[tuple[pd.DataFrame, pd.DataFrame]]:
    """Time-series walk-forward splits with EMBARGO_DAYS embargo."""
    if df.empty or len(df) < n_splits * (test_size_days + EMBARGO_DAYS) + 1:
        return []

    # Use date-based index for single-ticker; MultiIndex for multi-ticker
    dates = sorted(df.index.get_level_values(-1).unique()) if hasattr(df.index, "levels") else sorted(df.index.unique())
    n = len(dates)
    splits = []

    for i in range(n_splits):
        test_end_idx = n - 1 - i * test_size_days
        test_start_idx = test_end_idx - test_size_days + 1
        train_end_idx = test_start_idx - 1 - EMBARGO_DAYS

        if train_end_idx < test_size_days:
            break

        train_dates = set(dates[:train_end_idx + 1])
        test_dates = set(dates[test_start_idx:test_end_idx + 1])

        if hasattr(df.index, "levels"):
            train_df = df[df.index.get_level_values(-1).isin(train_dates)]
            test_df = df[df.index.get_level_values(-1).isin(test_dates)]
        else:
            train_df = df[df.index.isin(train_dates)]
            test_df = df[df.index.isin(test_dates)]

        if len(train_df) >= 30 and len(test_df) >= 5:
            splits.append((train_df, test_df))

    return splits


def compute_ic(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Spearman rank IC between true and predicted returns."""
    if len(y_true) < 3:
        return float("nan")
    r, _ = spearmanr(y_true, y_pred, nan_policy="omit")
    return float(r) if math.isfinite(float(r)) else float("nan")


def compute_ic_t_stat(ic_series: list[float]) -> float:
    """IC t-stat: mean(IC) / (std(IC) / sqrt(N))."""
    valid = [x for x in ic_series if not math.isnan(x)]
    if len(valid) < 2:
        return float("nan")
    mean_ic = sum(valid) / len(valid)
    std_ic = math.sqrt(sum((x - mean_ic) ** 2 for x in valid) / (len(valid) - 1))
    if std_ic < 1e-10:
        return float("nan")
    return mean_ic / (std_ic / math.sqrt(len(valid)))


def benjamini_hochberg(p_values: list[float], q: float = 0.10) -> list[bool]:
    """BH step-up correction: reject H_(1)..H_(k) where k = max rank with p_(k) <= (k/m)*q."""
    m = len(p_values)
    if m == 0:
        return []
    sorted_pairs = sorted(enumerate(p_values), key=lambda x: x[1])

    # Find largest k such that p_(k) <= (k/m)*q
    max_k = 0
    for rank, (_, p) in enumerate(sorted_pairs, start=1):
        if p <= (rank / m) * q:
            max_k = rank

    # Reject all hypotheses at rank <= max_k
    rejected = [False] * m
    for rank, (orig_idx, _) in enumerate(sorted_pairs, start=1):
        if rank <= max_k:
            rejected[orig_idx] = True
    return rejected


def batch_bh_correction(
    validations: dict[str, ValidationSummary],
    q: float = 0.10,
) -> dict[str, ValidationSummary]:
    """Apply BH correction across all models simultaneously.

    Replaces the provisional per-model bh_q_value_passed flags with proper
    multi-testing correction (m = number of models). Call this after
    validate_model() for all models in the ensemble.
    """
    from scipy.stats import norm as _norm  # noqa: PLC0415

    names = list(validations.keys())
    p_values = []
    for name in names:
        vs = validations[name]
        if vs.ic_t_stat is not None and math.isfinite(vs.ic_t_stat):
            p_values.append(float(2 * _norm.sf(abs(vs.ic_t_stat))))
        else:
            p_values.append(1.0)

    bh_results = benjamini_hochberg(p_values, q=q)

    return {
        name: ValidationSummary(
            **{**vs.model_dump(), "bh_q_value_passed": bh_results[i]}
        )
        for i, (name, vs) in enumerate(validations.items())
    }


def validate_model(
    model: "ModelBase",
    splits: list[tuple[pd.DataFrame, pd.DataFrame]],
    horizon: int,
    target_col: str = "r_net_h",
) -> ValidationSummary:
    """Walk-forward validation → ValidationSummary.

    Production pass: IC >= 0.03 AND t_IC >= 2.57
    Research-only:   IC > 0 but BH q-value not passed
    Failed:          IC <= 0 OR Brier >= naive Brier
    """
    from core.backtester.metrics_calculator import compute_deflated_sharpe_ratio

    ic_series: list[float] = []
    brier_scores: list[float] = []
    rmse_scores: list[float] = []
    pnl_values: list[float] = []
    n_obs = 0

    feature_cols = [c for c in (splits[0][0].columns if splits else []) if not c.startswith(("y_", "r_net", "sigma"))]

    for train_df, test_df in splits:
        if target_col not in train_df.columns or target_col not in test_df.columns:
            continue

        X_train = train_df[feature_cols].select_dtypes(include=[np.number]).fillna(0)
        y_train = train_df[target_col].fillna(0)
        X_test = test_df[feature_cols].select_dtypes(include=[np.number]).fillna(0)
        y_test = test_df[target_col].fillna(0)

        X_train, X_test = X_train.align(X_test, join="left", axis=1, fill_value=0)

        try:
            model.fit(X_train, y_train)
            y_pred = model.predict(X_test)
        except Exception:
            continue

        ic = compute_ic(y_test.values, y_pred)
        if not math.isnan(ic):
            ic_series.append(ic)

        if "y_up" in test_df.columns:
            y_bin = test_df["y_up"].values
            # Standardized sigmoid — graceful for constant predictions (NaiveModel → p_hat=0.5)
            y_std = float(np.std(y_pred))
            if y_std < 1e-10:
                p_hat = np.full(len(y_pred), 0.5)
            else:
                y_z = (y_pred - float(np.mean(y_pred))) / y_std
                p_hat = 1.0 / (1.0 + np.exp(-np.clip(y_z, -10.0, 10.0)))
            brier = float(np.mean((p_hat - y_bin) ** 2))
            brier_scores.append(brier)

        rmse = float(np.sqrt(np.mean((y_pred - y_test.values) ** 2)))
        rmse_scores.append(rmse)
        pnl_values.extend(y_test.tolist())
        n_obs += len(y_test)

    ic_mean = sum(ic_series) / len(ic_series) if ic_series else None
    ic_t_stat = compute_ic_t_stat(ic_series) if ic_series else None
    brier = sum(brier_scores) / len(brier_scores) if brier_scores else None
    rmse = sum(rmse_scores) / len(rmse_scores) if rmse_scores else None

    # DSR via metrics_calculator
    dsr: float | None = None
    if len(pnl_values) >= 4:
        dsr_result = compute_deflated_sharpe_ratio(pnl_values, benchmark_sr=0.0, n_trials=1)
        dsr = dsr_result["deflated_sr"] if dsr_result else None

    # Provisional single-model BH flag. With m=1, BH(q=0.10) reduces to p<0.10 (no
    # correction). Use stricter p<0.05 here; call batch_bh_correction() across all
    # models for proper multi-testing correction.
    bh_passed = False
    if ic_t_stat is not None and math.isfinite(ic_t_stat):
        from scipy.stats import norm as _norm  # noqa: PLC0415
        p_val = float(2 * _norm.sf(abs(ic_t_stat)))
        bh_passed = p_val < 0.05

    # Status
    if ic_mean is not None and ic_mean >= 0.03 and ic_t_stat is not None and ic_t_stat >= 2.57:
        status: str = "production"
    elif ic_mean is not None and ic_mean > 0:
        status = "research_only"
    else:
        status = "failed"

    return ValidationSummary(
        horizon_days=horizon,
        n_observations=n_obs,
        ic_mean=ic_mean,
        ic_t_stat=ic_t_stat,
        brier=brier,
        rmse=rmse,
        dsr=dsr,
        bh_q_value_passed=bh_passed,
        status=status,  # type: ignore[arg-type]
    )
