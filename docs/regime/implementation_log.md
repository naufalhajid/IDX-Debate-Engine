# Regime System — Implementation Log

## 2026-06-26

### Summary

9-phase implementation of the HMM-based market regime detection system for the IDX Debate Chamber. Replaces the single vol-threshold `classify_regime()` call with a 3-state Hidden Markov Model (BULL / SIDEWAYS / BEAR_STRESS) integrated as a LangGraph gate node before the scout fan-out.

---

### Phase 1 — Market Parameters (`core/idx_market_params.py`)

Added `IHSG_ATH_2026`, `IHSG_LOW_2026`, `IHSG_CURRENT`, and `MSCI_REVIEW_ACTIVE = True` to capture the June-November 2026 MSCI rebalancing risk context.

Added `HMM_TO_LEGACY_REGIME` bridge dict and `REGIME_RULES` dict (position limits, R/R floors, regime multipliers per label).

---

### Phase 2 — HMM Core (`core/regime_hmm.py`)

`IDXRegimeDetector` with:
- `GaussianHMM(n_components=3, covariance_type="full", n_iter=200)`
- Multi-start via `n_random_inits=10` seeds; best log-likelihood converged model kept
- State labeling by ascending `means_[:, 0]` (ihsg_return) → BEAR_STRESS / SIDEWAYS / BULL
- Atomic pickle write via `tempfile.mkstemp` + `Path.replace`
- MSCI override: forces BEAR_STRESS when `P(BEAR_STRESS) > 0.40` and `msci_review_active=True`

**Feature evolution**: started with `realized_vol_20d`. Phase 7 validation (ADF test) found it non-stationary (p=0.49). Replaced with `vol_zscore_63d = (vol_20d - mean_63d) / std_63d` — stationary (p=0.0005) and semantically better (relative elevation removes the GARCH trend).

---

### Phase 3 — Legacy Bridge (`utils/trade_math.py`, `utils/technicals.py`)

Extended `REGIME_RR_SCALING` and `REGIME_ATR_STOP_MULTIPLIER` with HMM labels so risk math works with either vocabulary.

---

### Phase 4 — Schema (`schemas/debate.py`)

Added `regime: NotRequired[dict]`, `trading_params: NotRequired[dict]`, `should_trade: NotRequired[bool]` to `DebateChamberState`. `NotRequired` preserves backwards compatibility with existing state dicts.

---

### Phase 5 — LangGraph Gate (`core/regime_gate.py`)

`regime_gate_node` fetches `^JKSE`, calls `detector.predict()`, writes `regime` / `trading_params` / `should_trade` into state. `regime_gate_router` routes to `"scout_dispatcher"` or `"trading_halted"`.

Module-level singletons: `_detector` (not re-fitted per ticker) and `_ihsg_prices_cache` with 4-hour TTL (one network call per batch — W5 fix).

---

### Phase 6 — Pipeline Integration

`services/debate_chamber.py`: `START → regime_gate → [scout_dispatcher | trading_halted]`. Scout dispatcher fans out to three scouts via `add_edge`. `_apply_defensive_clamp` extended to check both regime vocabularies.

`core/risk_governor.py`: `apply_defensive_guard` extended to check `hmm_label == "BEAR_STRESS"`.

`core/orchestrator/legacy.py`: `"regime": result.get("regime")` forwarded in result builder so `apply_defensive_guard` can read it (W4 fix).

---

### 360° Review Fixes

| ID | Fix |
| --- | --- |
| C1 | `_fit_feature_names` frozen snapshot; name comparison before `scaler.transform()` |
| W1 | Skip seeds where `monitor_.converged == False`; keep existing model if all fail |
| W2 | MSCI override updates `label`, `state_idx`, AND `confidence` consistently |
| W3 | Atomic pickle write via `mkstemp` + `Path.replace` |
| W4 | `regime` key forwarded into orchestrator result builder |
| W5 | Module-level IHSG price cache (4h TTL); stale fallback on network failure |
| N1 | `_get_detector()` logs INFO when `_load_model()` returns True/False |

---

### Phase 7 — Validation (`scripts/validate_regime.py`)

Five tests: V1 stationarity, V2 persistence, V3 MSCI override, V4 walk-forward, V5 drawdown alignment. All pass on live `^JKSE` data. V1 drove the `realized_vol_20d` → `vol_zscore_63d` feature change.

---

### Phase 8 — Unit Tests (`tests/test_regime_hmm.py`)

8 tests pinning each fix: C1 name mismatch, W1 convergence skip, W2a/W2b override, W3 atomic write + round-trip, N1 logging, walk-forward scaler stability.

---

### Files Changed

| File | Change |
| --- | --- |
| `core/regime_hmm.py` | New |
| `core/regime_gate.py` | New |
| `core/idx_market_params.py` | REGIME_RULES, HMM_TO_LEGACY_REGIME, MSCI flag |
| `schemas/debate.py` | regime / trading_params / should_trade fields |
| `services/debate_chamber.py` | Gate rewire, scout_dispatcher, trading_halted, clamp extension |
| `core/risk_governor.py` | BEAR_STRESS check |
| `core/orchestrator/legacy.py` | regime key in result builder |
| `utils/trade_math.py` | HMM labels in REGIME_RR_SCALING |
| `utils/technicals.py` | HMM labels in REGIME_ATR_STOP_MULTIPLIER |
| `scripts/validate_regime.py` | New |
| `tests/test_regime_hmm.py` | New |
| `docs/regime/REGIME_SYSTEM.md` | New |
| `docs/regime/implementation_log.md` | This file |
