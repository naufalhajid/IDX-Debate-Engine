"""Tests for ensemble weighting — zero-weight, BH correction, softmax."""
from __future__ import annotations


import pytest

from core.forecasting.ensemble import compute_ensemble_weights
from core.forecasting.validation import benjamini_hochberg


def _scores(ic: float, brier: float = 0.2, bh_passed: bool = True) -> dict:
    return {"ic": ic, "rmse": 0.01, "brier": brier, "dsr": 0.6, "bh_passed": bh_passed}


class TestZeroWeightConditions:
    def test_negative_ic_gives_zero_weight(self):
        """IC < 0 → model excluded (weight = 0)."""
        scores = {
            "model_a": _scores(ic=0.05),
            "model_b": _scores(ic=-0.01),
        }
        weights = compute_ensemble_weights(scores)

        assert weights["model_b"] == pytest.approx(0.0)
        assert weights["model_a"] == pytest.approx(1.0)

    def test_bh_failed_gives_zero_weight(self):
        """BH q-value not passed → model excluded."""
        scores = {
            "model_a": _scores(ic=0.05, bh_passed=True),
            "model_b": _scores(ic=0.04, bh_passed=False),
        }
        weights = compute_ensemble_weights(scores)

        assert weights["model_b"] == pytest.approx(0.0)
        assert weights["model_a"] == pytest.approx(1.0)

    def test_brier_gte_naive_gives_zero_weight(self):
        """Brier >= naive Brier → model excluded."""
        scores = {
            "naive": _scores(ic=0.02, brier=0.25, bh_passed=True),
            "model_a": _scores(ic=0.05, brier=0.30, bh_passed=True),
        }
        weights = compute_ensemble_weights(scores)

        assert weights["model_a"] == pytest.approx(0.0)

    def test_external_naive_brier_excludes_worse_model_when_naive_is_not_contributor(self):
        """A failed Naive model remains the calibration benchmark only."""
        scores = {
            "xgboost": _scores(ic=0.05, brier=0.26252, bh_passed=True),
        }

        weights = compute_ensemble_weights(
            scores,
            naive_brier_benchmark=0.25,
        )

        assert weights["xgboost"] == pytest.approx(0.0)

    def test_all_zero_weight_when_all_disqualified(self):
        scores = {"model_a": _scores(ic=-0.01), "model_b": _scores(ic=-0.02)}
        weights = compute_ensemble_weights(scores)
        assert all(w == pytest.approx(0.0) for w in weights.values())

    def test_weights_sum_to_one_when_any_active(self):
        scores = {"model_a": _scores(ic=0.05), "model_b": _scores(ic=0.03)}
        weights = compute_ensemble_weights(scores)
        assert sum(weights.values()) == pytest.approx(1.0, abs=1e-9)

    def test_empty_scores_returns_empty(self):
        assert compute_ensemble_weights({}) == {}


class TestBenjaminiHochberg:
    def test_bh_rejects_significant_p_values(self):
        p_values = [0.001, 0.010, 0.200]
        results = benjamini_hochberg(p_values, q=0.10)
        assert results[0] is True
        assert results[2] is False

    def test_bh_empty_returns_empty(self):
        assert benjamini_hochberg([], q=0.10) == []

    def test_bh_all_high_p_values_false(self):
        assert all(r is False for r in benjamini_hochberg([0.5, 0.6, 0.7], q=0.10))

    def test_bh_single_significant(self):
        assert benjamini_hochberg([0.001], q=0.10) == [True]

    def test_bh_single_not_significant(self):
        assert benjamini_hochberg([0.5], q=0.10) == [False]
