"""Softmax ensemble weighting for forecasting models."""
from __future__ import annotations

import math

from core.forecasting.schemas import ModelVote

ETA: float = 1.5  # softmax temperature


def compute_ensemble_weights(
    model_scores: dict[str, dict],
) -> dict[str, float]:
    """Compute softmax ensemble weights.

    S_m = 0.35*z(IC_m) + 0.25*z(-RMSE_m) + 0.25*z(-Brier_m) + 0.15*z(DSR_m)
    w_m = exp(ETA * S_m) / sum_j exp(ETA * S_j)

    Zero-weight conditions (model excluded):
      - IC_m < 0
      - BH q-value failed
      - Brier_m >= naive Brier (when naive Brier is available)
    Weights are renormalized after zeroing.

    Args:
        model_scores: dict of {model_name: {"ic": float, "rmse": float,
            "brier": float, "dsr": float, "bh_passed": bool}}

    Returns:
        dict of {model_name: weight} summing to 1.0 (or all 0.0 if all zero-weighted).
    """
    if not model_scores:
        return {}

    naive_brier = model_scores.get("naive", {}).get("brier")

    # Step 1: zero-weight disqualified models
    active: dict[str, dict] = {}
    for name, scores in model_scores.items():
        ic = scores.get("ic")
        bh_passed = bool(scores.get("bh_passed", False))
        brier = scores.get("brier")

        if ic is not None and ic < 0:
            continue
        if not bh_passed:
            continue
        if naive_brier is not None and brier is not None and brier >= naive_brier:
            continue
        dir_acc = scores.get("dir_acc")
        if dir_acc is not None and dir_acc < 0.45:
            continue
        active[name] = scores

    if not active:
        return {name: 0.0 for name in model_scores}

    # Step 2: z-score each metric across active models
    def _zscore(values: list[float]) -> list[float]:
        if len(values) < 2:
            return [0.0] * len(values)
        mean = sum(values) / len(values)
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        if std < 1e-10:
            return [0.0] * len(values)
        return [(v - mean) / std for v in values]

    names = list(active.keys())
    ics = [active[n].get("ic") or 0.0 for n in names]
    rmses = [active[n].get("rmse") or 0.0 for n in names]
    briers = [active[n].get("brier") or 0.0 for n in names]
    dsrs = [active[n].get("dsr") or 0.0 for n in names]

    z_ic = _zscore(ics)
    z_neg_rmse = _zscore([-r for r in rmses])
    z_neg_brier = _zscore([-b for b in briers])
    z_dsr = _zscore(dsrs)

    # Step 3: composite score
    raw_scores: dict[str, float] = {}
    for i, name in enumerate(names):
        s = (
            0.35 * z_ic[i]
            + 0.25 * z_neg_rmse[i]
            + 0.25 * z_neg_brier[i]
            + 0.15 * z_dsr[i]
        )
        raw_scores[name] = s

    # Step 4: softmax
    max_s = max(raw_scores.values())
    exp_scores = {n: math.exp(ETA * (s - max_s)) for n, s in raw_scores.items()}
    total = sum(exp_scores.values())

    weights: dict[str, float] = {name: 0.0 for name in model_scores}
    if total > 1e-12:
        for name in names:
            weights[name] = exp_scores[name] / total

    return weights


def blend_votes(votes: list[ModelVote]) -> tuple[float | None, float | None, float | None]:
    """Weighted average of r_hat_net, p_target, p_stop across model votes."""
    total_w = sum(v.weight for v in votes if v.weight > 0)
    if total_w < 1e-12:
        return None, None, None

    r_hat = sum((v.r_hat_net or 0.0) * v.weight for v in votes if v.weight > 0) / total_w
    p_target = sum((v.p_target or 0.0) * v.weight for v in votes if v.weight > 0) / total_w
    p_stop = sum((v.p_stop or 0.0) * v.weight for v in votes if v.weight > 0) / total_w

    return r_hat, p_target, p_stop
