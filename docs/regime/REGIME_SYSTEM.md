# Regime System

The regime system detects the current IHSG market state and gates the debate pipeline before any LLM work begins. It sits between `START` and the parallel scout fan-out in the LangGraph graph.

## Architecture

```
START
  └─► regime_gate_node          # fetches ^JKSE, fits/predicts HMM
        ├─► scout_dispatcher    # should_trade=True  → proceeds to scouts
        │     ├─► fundamental
        │     ├─► chartist
        │     └─► sentiment
        └─► trading_halted      # should_trade=False → emits HOLD immediately
```

**Key modules**

| Module | Role |
| --- | --- |
| `core/regime_hmm.py` | `IDXRegimeDetector` — HMM fit, predict, cache |
| `core/regime_gate.py` | LangGraph node + router; singleton detector + IHSG cache |
| `core/idx_market_params.py` | `REGIME_RULES`, `HMM_TO_LEGACY_REGIME`, market constants |
| `schemas/debate.py` | `DebateChamberState` fields: `regime`, `trading_params`, `should_trade` |

## HMM Model

**Algorithm**: `GaussianHMM(n_components=3, covariance_type="full", n_iter=200)`

**Features** (3 when only IHSG prices are available; 5 with optional inputs)

| Column | Formula | Why |
| --- | --- | --- |
| `ihsg_return` | `log(P_t / P_{t-1})` | Primary HMM observation |
| `vol_zscore_63d` | `(vol_20d - mean_63d) / std_63d` | Relative vol; stationary unlike raw/log vol |
| `momentum_5d` | `P_t / P_{t-5} - 1` | Distinguishes corrections from regime shifts |
| `usd_idr_change` | `log(USD/IDR_t / USD/IDR_{t-1})` | IDR weakening leads IHSG drops 1-2 days |
| `foreign_flow_zscore` | 60-day z-score of net foreign flow | Most predictive IDX-specific feature |

Optional inputs (`usd_idr`, `foreign_flow`) degrade gracefully — the model works with 3 features if unavailable.

**Training**: 756 trading days minimum. `n_random_inits=10` seeds; the highest log-likelihood converged model is kept. Seeds that do not converge (`monitor_.converged == False`) are skipped.

**State labeling**: states sorted by `model.means_[:, 0]` (ihsg_return mean, ascending). Mapping: index 0 = BEAR_STRESS, 1 = SIDEWAYS, 2 = BULL.

## State Labels

| Label | Typical return | Position limit | R/R floor |
| --- | --- | --- | --- |
| `BULL` | positive | 100% of normal | 2.0 |
| `SIDEWAYS` | near zero | 80% | 2.2 |
| `BEAR_STRESS` | negative | 50% | 2.5 |
| `UNKNOWN` | -- | 0% (no trading) | -- |

`UNKNOWN` is emitted only when the HMM is not fitted and no cached IHSG data is available.

## MSCI Override

When `msci_review_active=True` and `P(BEAR_STRESS) > 0.40`, the detector forces BEAR_STRESS regardless of the argmax state. This reflects the June-November 2026 MSCI rebalancing risk for Indonesian equities. The override updates `label`, `state_idx`, and `confidence` together so they are self-consistent.

Set `MSCI_REVIEW_ACTIVE` in `core/idx_market_params.py`. Currently `True` — disable after November 2026.

## Legacy Bridge

The pipeline uses two vocabulary systems simultaneously: the old 5-state vol-threshold system (DEFENSIVE / RECOVERY / HIGH / NORMAL / LOW) and the new 3-state HMM. `HMM_TO_LEGACY_REGIME` in `idx_market_params.py` maps between them:

```
BULL         -> NORMAL
SIDEWAYS     -> HIGH
BEAR_STRESS  -> DEFENSIVE
UNKNOWN      -> DEFENSIVE
```

`_apply_defensive_clamp` (debate chamber) and `apply_defensive_guard` (risk governor) check both:

```python
is_defensive = legacy_regime == "DEFENSIVE" or hmm_label == "BEAR_STRESS"
```

## Caching

**Model cache**: `models/regime_hmm_cache.pkl`. Written atomically via `tempfile.mkstemp` + `Path.replace` — a partial write never corrupts the existing cache. Retrained when `last_trained` is older than `retrain_frequency_days` (default: 7 days). Stores: model, scaler, state label map, `fit_feature_names`, and timestamp.

**IHSG price cache**: module-level `_ihsg_prices_cache` in `regime_gate.py` with a 4-hour TTL. All tickers in a batch share one network round-trip. On transient network failure with a warm cache, stale prices are used with a WARNING log rather than returning UNKNOWN for the whole batch.

## Walk-Forward Safety

`scaler.fit_transform()` is called only inside `fit()`. `predict()` uses `scaler.transform()` only. Before transform, `_feature_names` (predict-time) is compared against `_fit_feature_names` (frozen at fit-time). A name mismatch — including same-count column renames — triggers an automatic re-fit rather than silently passing the wrong columns through the scaler.

## Configuration

| Parameter | Default | Notes |
| --- | --- | --- |
| `n_states` | `3` | Fixed: BULL / SIDEWAYS / BEAR_STRESS |
| `training_window_days` | `756` | ~3 years of trading days |
| `retrain_frequency_days` | `7` | Weekly retrain |
| `n_random_inits` | `10` | Multi-start to escape local optima |
| `msci_review_active` | `True` | Disable after November 2026 |
| `model_cache_path` | `models/regime_hmm_cache.pkl` | Override in tests with `tmp_path` |

## Ops

**Validate**: `uv run python scripts/validate_regime.py` — 5 tests covering stationarity, persistence, override logic, walk-forward integrity, and regime-drawdown alignment.

**Force retrain**: `detector.fit(prices, force_retrain=True)` or delete `models/regime_hmm_cache.pkl`.

**Disable MSCI override** (after November 2026): set `MSCI_REVIEW_ACTIVE = False` in `core/idx_market_params.py`, then delete the model cache to retrain without the override.

**BEAR_STRESS warning**: the detector logs WARNING when `confidence > 85%` so operators see it before running the pipeline in a stressed market.
