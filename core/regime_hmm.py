"""
core/regime_hmm.py — IDX Market Regime Detector (HMM-based)

Detects 3 market regimes using a Gaussian Hidden Markov Model calibrated
for IDX/BEI, with features chosen for the Indonesian market structure.

Research basis:
  MARCD (arXiv 2510.10807)        K=3 BIC-optimal, 3-year window, walk-forward
  Downside Risk (arXiv 2402.05272) 10 random inits, last-state extraction
  Forest of Opinions (DSFE 2025)  3-state (bull/neutral/bear) ensemble
  BAREKENG IDX                    Markov-switching validated on JII stocks

Regime states:
  BULL        — positive returns, low vol, foreign net buy
  SIDEWAYS    — flat returns, moderate vol (includes RECOVERY bounces)
  BEAR_STRESS — negative returns, high vol, foreign net sell

IDX context (Jun 2026):
  IHSG ATH 9,174 (Jan 9) → low 5,317 (Jun 8) → ~6,137 current
  YTD: -29% | MSCI review extended to Nov 2026

Walk-forward guarantee:
  scaler.fit_transform() only on training window
  scaler.transform() at predict time — never refit
  predict_proba on full sequence; posterior at the last row equals the
  forward-only probability (no lookahead bias at the terminal observation)
"""

from __future__ import annotations

import logging
import os
import pickle
import tempfile
import warnings
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn import hmm
from sklearn.preprocessing import StandardScaler

from core.idx_market_params import MSCI_REVIEW_ACTIVE, REGIME_RULES

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_PATH = (
    Path(__file__).resolve().parent.parent / "models" / "regime_hmm.pkl"
)


@dataclass
class RegimeState:
    """Output of a single IDX regime prediction."""

    label: str          # "BULL" | "SIDEWAYS" | "BEAR_STRESS" | "UNKNOWN"
    state_idx: int      # raw HMM state index (0..n_states-1); -1 when unknown
    confidence: float   # posterior probability of the winning state (0–1)
    probabilities: dict  # {"BULL": 0.1, "SIDEWAYS": 0.2, "BEAR_STRESS": 0.7}
    features_used: list  # feature column names passed to HMM
    training_days: int  # rows used in most-recent fit
    detected_at: str    # ISO-8601 timestamp
    msci_override: bool  # True when MSCI logic forced BEAR_STRESS label
    notes: str = ""


class IDXRegimeDetector:
    """
    Gaussian HMM regime detector calibrated for IDX/BEI.

    Typical usage
    -------------
    detector = IDXRegimeDetector()
    detector.fit(ihsg_prices)              # also accepts usd_idr, foreign_flow
    state = detector.predict(ihsg_prices)
    rules = detector.get_trading_rules(state)
    print(detector.summary(state))
    """

    def __init__(
        self,
        n_states: int = 3,
        training_window_days: int = 756,      # ~3 years; MARCD paper
        n_random_inits: int = 10,             # Downside Risk paper: "ten runs"
        retrain_frequency_days: int = 5,      # weekly; daily too expensive
        model_cache_path: str | Path | None = None,
        msci_review_active: bool = MSCI_REVIEW_ACTIVE,
    ):
        self.n_states = n_states
        self.training_window_days = training_window_days
        self.n_random_inits = n_random_inits
        self.retrain_frequency_days = retrain_frequency_days
        self.msci_review_active = msci_review_active
        self.model_cache_path = (
            Path(model_cache_path) if model_cache_path else _DEFAULT_MODEL_PATH
        )

        self.model: Optional[hmm.GaussianHMM] = None
        self.scaler: StandardScaler = StandardScaler()
        self.last_trained: Optional[datetime] = None
        self.state_label_map: dict[int, str] = {}
        self._feature_names: list[str] = []      # mutable — set by build_features() each call
        self._fit_feature_names: list[str] = []  # frozen snapshot set only by fit()

    # ─────────────────────────────────────────────────────────────────────────
    # Feature engineering
    # ─────────────────────────────────────────────────────────────────────────

    def build_features(
        self,
        ihsg_prices: pd.Series,
        usd_idr: Optional[pd.Series] = None,
        foreign_flow: Optional[pd.Series] = None,
    ) -> tuple[np.ndarray, pd.Index]:
        """
        Build the HMM feature matrix.

        Returns (array of shape (T, n_features), DatetimeIndex of length T).
        Column 0 is always ihsg_return — used for state labeling in _assign_state_labels.
        Optional inputs degrade gracefully (logged but do not raise).
        """
        feats: dict[str, pd.Series] = {}

        # Feature 1: IHSG log returns — primary HMM observation
        log_ret = np.log(ihsg_prices / ihsg_prices.shift(1)).dropna()
        feats["ihsg_return"] = log_ret

        # Feature 2: Vol z-score — (vol - 63d mean) / 63d std; stationary unlike raw/log vol
        _vol_20d = log_ret.rolling(20).std()
        _vol_mean = _vol_20d.rolling(63).mean()
        _vol_std  = _vol_20d.rolling(63).std().replace(0.0, np.nan)
        feats["vol_zscore_63d"] = (_vol_20d - _vol_mean) / _vol_std

        # Feature 3: USD/IDR log return — IDR weakening precedes IHSG drops 1-2 days
        if usd_idr is not None and len(usd_idr) > 1:
            aligned = usd_idr.reindex(log_ret.index, method="ffill")
            feats["usd_idr_change"] = np.log(aligned / aligned.shift(1))

        # Feature 4: Foreign flow z-score — most predictive IDX-specific feature;
        # Rp 53-80 triliun outflow coincided with the BEAR_STRESS regime in 2026
        if foreign_flow is not None and len(foreign_flow) > 60:
            aligned_ff = foreign_flow.reindex(log_ret.index, method="ffill")
            roll_mean = aligned_ff.rolling(60).mean()
            roll_std = aligned_ff.rolling(60).std().replace(0, np.nan)
            feats["foreign_flow_zscore"] = (aligned_ff - roll_mean) / roll_std

        # Feature 5: 5-day momentum — distinguishes corrections from regime shifts
        feats["momentum_5d"] = ihsg_prices.pct_change(5).reindex(log_ret.index)

        df = pd.DataFrame(feats).dropna()
        self._feature_names = list(df.columns)

        min_rows = self.n_states * 20
        if len(df) < min_rows:
            logger.warning(
                "[RegimeHMM] Only %d rows after dropna (need >=%d for %d states).",
                len(df), min_rows, self.n_states,
            )

        return df.values, df.index

    # ─────────────────────────────────────────────────────────────────────────
    # Training
    # ─────────────────────────────────────────────────────────────────────────

    def fit(
        self,
        ihsg_prices: pd.Series,
        usd_idr: Optional[pd.Series] = None,
        foreign_flow: Optional[pd.Series] = None,
        force_retrain: bool = False,
    ) -> "IDXRegimeDetector":
        """Fit (or retrain) the HMM on the most-recent training_window_days rows."""
        if not force_retrain and not self.should_retrain():
            logger.debug("[RegimeHMM] Skipping retrain — within retrain window.")
            return self

        features, _ = self.build_features(ihsg_prices, usd_idr, foreign_flow)
        min_rows = self.n_states * 20
        if len(features) < min_rows:
            logger.error(
                "[RegimeHMM] Fit aborted — %d rows < minimum %d.",
                len(features), min_rows,
            )
            # Restore _feature_names to stay aligned with the unchanged model/scaler (Fix 6).
            self._feature_names = list(self._fit_feature_names)
            return self

        window = features[-self.training_window_days:]
        # Use a candidate scaler; only promote to self.scaler if a model converges.
        # Promoting eagerly would misalign scaler/model when all seeds fail (Fix 1).
        _candidate_scaler = StandardScaler()
        X = _candidate_scaler.fit_transform(window)

        best_model: Optional[hmm.GaussianHMM] = None
        best_score = -np.inf
        n_converged = 0

        for seed in range(self.n_random_inits):
            candidate = hmm.GaussianHMM(
                n_components=self.n_states,
                covariance_type="full",
                n_iter=200,
                random_state=seed,
                tol=1e-4,
            )
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    candidate.fit(X)
                if not candidate.monitor_.converged:
                    logger.debug(
                        "[RegimeHMM] seed=%d did not converge after %d iters — skipping.",
                        seed, candidate.n_iter,
                    )
                    continue
                n_converged += 1
                score = candidate.score(X)
                if score > best_score:
                    best_score = score
                    best_model = candidate
            except Exception as exc:
                logger.debug("[RegimeHMM] seed=%d failed: %s", seed, exc)

        if best_model is None:
            if n_converged == 0:
                logger.error(
                    "[RegimeHMM] All %d seeds failed to converge — keeping existing model.",
                    self.n_random_inits,
                )
            else:
                logger.error(
                    "[RegimeHMM] All %d initializations failed.", self.n_random_inits
                )
            # Restore _feature_names so it stays aligned with the unchanged model/scaler
            # (Fix 6: prevents spurious forced-refit on next predict()).
            self._feature_names = list(self._fit_feature_names)
            return self

        self.model = best_model
        self.scaler = _candidate_scaler  # promote only on success (Fix 1)
        self.last_trained = datetime.now()
        self._fit_feature_names = list(self._feature_names)  # freeze at fit time
        self._assign_state_labels()
        self._save_model()

        logger.info(
            "[RegimeHMM] Fitted | window=%d days | log-lik=%.1f | "
            "map=%s | features=%s",
            len(window), best_score, self.state_label_map, self._fit_feature_names,
        )
        return self

    def _assign_state_labels(self) -> None:
        """
        Map HMM state indices to BULL / SIDEWAYS / BEAR_STRESS.

        Sorts states by model.means_[:, 0] (scaled ihsg_return emission mean).
        Ascending sort: lowest mean → BEAR_STRESS, highest → BULL.
        Using emission means is more stable than Viterbi-decoded sequences and
        does not depend on sufficient per-state sample counts.
        """
        ihsg_means = self.model.means_[:, 0]
        sorted_idx = np.argsort(ihsg_means)    # ascending: [bear ... bull]
        # Build label list that scales with n_states: lowest mean = BEAR_STRESS,
        # highest = BULL, all middle states = SIDEWAYS.  Avoids IndexError for n_states > 3.
        labels = (
            ["BEAR_STRESS"]
            + ["SIDEWAYS"] * max(0, self.n_states - 2)
            + ["BULL"]
        )
        self.state_label_map = {
            int(sorted_idx[i]): labels[i] for i in range(self.n_states)
        }

    # ─────────────────────────────────────────────────────────────────────────
    # Prediction
    # ─────────────────────────────────────────────────────────────────────────

    def predict(
        self,
        ihsg_prices: pd.Series,
        usd_idr: Optional[pd.Series] = None,
        foreign_flow: Optional[pd.Series] = None,
    ) -> RegimeState:
        """
        Predict today's market regime with strict walk-forward guarantee.

        Uses predict_proba on the full feature sequence; the posterior at the
        last position equals the forward-only probability because there are no
        future observations — zero lookahead bias at the terminal step.
        """
        if self.model is None or self.should_retrain():
            self.fit(ihsg_prices, usd_idr, foreign_flow)

        if self.model is None:
            return self._unknown_state("HMM fit failed — insufficient data")

        features, _ = self.build_features(ihsg_prices, usd_idr, foreign_flow)

        # C1 fix: catch same-count but different-column misalignment.
        # build_features() just overwrote self._feature_names with the predict-time
        # set; compare against the frozen training-time snapshot before transforming.
        if self._fit_feature_names and self._feature_names != self._fit_feature_names:
            logger.warning(
                "[RegimeHMM] Feature set changed %s -> %s — re-fitting to prevent scaler misalignment.",
                self._fit_feature_names,
                self._feature_names,
            )
            self.fit(ihsg_prices, usd_idr, foreign_flow, force_retrain=True)
            features, _ = self.build_features(ihsg_prices, usd_idr, foreign_flow)

        try:
            X = self.scaler.transform(features)
        except ValueError as exc:
            # Count mismatch — re-fit as last resort.
            logger.warning("[RegimeHMM] Feature count mismatch (%s) — re-fitting.", exc)
            self.fit(ihsg_prices, usd_idr, foreign_flow, force_retrain=True)
            if self.model is None:
                return self._unknown_state(f"Refit after mismatch failed: {exc}")
            features, _ = self.build_features(ihsg_prices, usd_idr, foreign_flow)
            try:
                X = self.scaler.transform(features)
            except ValueError as exc2:
                return self._unknown_state(f"Scaler mismatch persists after refit: {exc2}")

        # Forward-backward posteriors: last row is causal (see module docstring).
        posteriors = self.model.predict_proba(X)    # (T, n_states)
        last_post = posteriors[-1]                   # (n_states,)
        state_idx = int(np.argmax(last_post))
        label = self.state_label_map.get(state_idx, "UNKNOWN")
        confidence = float(last_post[state_idx])

        probs = {
            self.state_label_map.get(i, f"state_{i}"): float(last_post[i])
            for i in range(self.n_states)
        }

        # MSCI override: err on the side of caution until Nov 2026 review resolves.
        # P(BEAR_STRESS) > 40% while MSCI review active → force label to BEAR_STRESS.
        # When override fires, update label, state_idx AND confidence to stay self-consistent
        # so downstream consumers that compare label against probabilities/state_idx are correct.
        msci_override = False
        if self.msci_review_active and probs.get("BEAR_STRESS", 0.0) > 0.40:
            if label != "BEAR_STRESS":
                bear_idx = next(
                    (k for k, v in self.state_label_map.items() if v == "BEAR_STRESS"),
                    state_idx,
                )
                label = "BEAR_STRESS"
                state_idx = bear_idx
                confidence = float(last_post[bear_idx])
                msci_override = True
                logger.info(
                    "[RegimeHMM] MSCI override → BEAR_STRESS "
                    "(P(bear)=%.1f%%, review active until Nov 2026)",
                    probs["BEAR_STRESS"] * 100,
                )

        if label == "BEAR_STRESS" and confidence > 0.85:
            logger.warning(
                "[RegimeHMM] BEAR_STRESS confidence %.1f%% > 85%% — "
                "consider pausing trading.",
                confidence * 100,
            )

        return RegimeState(
            label=label,
            state_idx=state_idx,
            confidence=confidence,
            probabilities=probs,
            features_used=list(self._feature_names),
            training_days=min(len(features), self.training_window_days),
            detected_at=datetime.now().isoformat(),
            msci_override=msci_override,
            notes=f"MSCI review active: {self.msci_review_active}",
        )

    def _unknown_state(self, reason: str) -> RegimeState:
        return RegimeState(
            label="UNKNOWN",
            state_idx=-1,
            confidence=0.0,
            probabilities={"BULL": 0.0, "SIDEWAYS": 0.0, "BEAR_STRESS": 0.0},
            features_used=list(self._feature_names),
            training_days=0,
            detected_at=datetime.now().isoformat(),
            msci_override=False,
            notes=reason,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Persistence
    # ─────────────────────────────────────────────────────────────────────────

    def _save_model(self) -> None:
        self.model_cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model": self.model,
            "scaler": self.scaler,
            "state_label_map": self.state_label_map,
            "fit_feature_names": self._fit_feature_names,
            "last_trained": self.last_trained,
        }
        # Write to a temp file in the same directory then rename so concurrent
        # readers never see a partially-written pickle (W3 fix).
        fd, tmp_path = tempfile.mkstemp(
            dir=self.model_cache_path.parent, suffix=".pkl.tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                pickle.dump(payload, fh)
            Path(tmp_path).replace(self.model_cache_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.info("[RegimeHMM] Model saved → %s", self.model_cache_path)

    def _load_model(self) -> bool:
        """Load a previously saved model. Returns True on success."""
        if not self.model_cache_path.exists():
            return False
        try:
            with open(self.model_cache_path, "rb") as fh:
                data = pickle.load(fh)
            self.model = data["model"]
            self.scaler = data["scaler"]
            self.state_label_map = data["state_label_map"]
            # "fit_feature_names" is the current key; "feature_names" is the old key
            # from pickles written before the C1 fix — fall back gracefully.
            self._fit_feature_names = data.get(
                "fit_feature_names", data.get("feature_names", [])
            )
            self._feature_names = list(self._fit_feature_names)
            self.last_trained = data["last_trained"]
            logger.info("[RegimeHMM] Model loaded ← %s", self.model_cache_path)
            return True
        except Exception as exc:
            logger.warning("[RegimeHMM] Load failed: %s", exc)
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Utilities
    # ─────────────────────────────────────────────────────────────────────────

    def should_retrain(self) -> bool:
        """True when no model exists or retrain_frequency_days has elapsed."""
        if self.model is None or self.last_trained is None:
            return True
        return (datetime.now() - self.last_trained).days >= self.retrain_frequency_days

    def get_trading_rules(self, regime_state: RegimeState) -> dict:
        """Return the REGIME_RULES entry matching the detected label."""
        return REGIME_RULES.get(regime_state.label, REGIME_RULES["UNKNOWN"])

    def summary(self, regime_state: RegimeState) -> str:
        """Return a human-readable one-block summary suitable for logging."""
        rules = self.get_trading_rules(regime_state)
        probs_str = " | ".join(
            f"{k}: {v:.0%}"
            for k, v in sorted(regime_state.probabilities.items())
        )
        lines = [
            f"== IHSG Regime: {regime_state.label} "
            f"(confidence: {regime_state.confidence:.1%})",
            f"   Probabilities: {probs_str}",
            f"   Max position: {rules['max_position_pct']:.1%} per trade  |  "
            f"Min R/R: {rules['min_risk_reward']}x  |  "
            f"Consensus: {rules['consensus_threshold']:.0%}  |  "
            f"Max concurrent: {rules['max_concurrent_positions']}",
            f"   Trading allowed: {rules['trading_allowed']}",
        ]
        if regime_state.msci_override:
            lines.append("   MSCI override active — extra caution applied")
        if regime_state.notes:
            lines.append(f"   Notes: {regime_state.notes}")
        return "\n".join(lines)
