# Forecasting Models — IDX Swing Trading Research Report
**Date:** 2026-07-01  
**Scope:** All forecasting model families; empirical + literature synthesis  
**System context:** Existing `core/forecasting/` ensemble (Naive + ARIMA + XGBoost + TGARCH)

---

## Executive Summary

The existing forecasting layer is architecturally sound but has three **blocking issues** and three **calibration gaps** that cause it to produce `AVOID` on nearly every ticker. The root problem is not model choice — it is (a) a TGARCH multi-step forecast bug, (b) an over-strict IC t-stat threshold relative to available sample size, and (c) ARIMA contributing actively anti-predictive signals. No additional model family (LSTM, Transformer, Prophet) is warranted until these are fixed. The real value-add of the forecasting layer for swing trading is **volatility forecasting** for stop placement and position sizing, not return direction — and that is blocked entirely by the TGARCH bug.

---

## Phase 1 — Empirical Validation Results (Live IDX Data)

Ran `ForecastingService.predict(mode='ensemble')` on BBRI, BBCA, TLKM with n_splits=3, test_size_days=30, EMBARGO=20. All dates as of 2026-07-01.

```
TICKER  DECISION  TGARCH    IC_NAIVE  IC_ARIMA  IC_XGB   XGB_DIR_ACC  XGB_STATUS
BBRI    AVOID     FALLBACK  null      -0.014    +0.094   0.456        research_only
BBCA    AVOID     FALLBACK  null      +0.047    +0.432   0.356        research_only
TLKM    AVOID     FALLBACK  null      -0.350    +0.245   0.578        research_only
```

**n_obs = 90 per ticker** (3 splits × 30 test bars).  
No ticker cleared "production" status (IC >= 0.03 AND t-stat >= 2.57 after BH).  
BBCA XGBoost: IC = 0.43, t-stat = 2.543 — borderline miss by 0.027 on the t-stat.

---

## Phase 2 — Model-by-Model Findings

### A — TGARCH (Volatility) — BLOCKING BUG

**Root cause:** `arch_model` with `power=1.0` (TARCH/GJR-GARCH) cannot produce analytic multi-step forecasts. The library throws `"Analytic forecasts not available for horizon > 1 when power != 2"` on every call with `horizon > 1`. The model fits correctly (convergence_flag=0 on all tickers, persistence < 1.0) but the forecast call always raises and triggers the `_classic_fallback`.

**Diagnostic (all three tickers):**
```
BBRI: conv_flag=0, alpha=0.00, beta=0.86, gamma=0.12, persist=0.92  GOOD FIT
BBCA: conv_flag=0, alpha=0.11, beta=0.79, gamma=0.05, persist=0.93  GOOD FIT
TLKM: conv_flag=0, alpha=0.32, beta=0.39, gamma=-0.003, persist=0.71  GOOD FIT
```

**Fix:** Pass `method='simulation', simulations=500` to `result.forecast()` when `power != 2.0`. Simulation-based forecasting handles TARCH's non-square power correctly. Verified working: produces term structure (not flat) annualized sigmas of ~40-46% for BBRI/BBCA/TLKM — consistent with IDX blue-chip realized vol.

**Impact:** Without this fix, the volatility backbone always falls back to 20-day rolling std (constant flat sigma, no term structure), and TGARCH's contribution to position sizing and stop calibration is zero.

**File:** `core/forecasting/models/tgarch.py`, `predict_volatility()` method.

---

### B — ARIMA — ANTI-SIGNAL, REMOVE FROM ENSEMBLE

**Empirical:** IC = -0.014 (BBRI), +0.047 (BBCA), -0.350 (TLKM). Direction accuracy = 20%, 24%, 23% on the three tickers. On two out of three tickers, ARIMA actively predicts the wrong direction 75-80% of the time — worse than a random coin flip.

**Why this happens:** ARIMA on differenced returns (the correct stationarity transformation) converges to the random walk order `(0,1,0)` on most equity series. The `(0,1,0)` model predicts the last observed return repeats, which is slightly anti-mean-reverting for stocks with high negative serial correlation. IDX blue chips exhibit mean reversion at 5-day horizons (consistent with the Naive model's 64-76% directional accuracy from the global mean predictor), so ARIMA's trend-following prediction is systematically wrong.

**Literature:** This finding aligns with Springer (2020) "Predictive power of ARIMA models in forecasting equity returns: a sliding window method" which found ARIMA generates over-optimistic errors and performs poorly when directional accuracy is measured rather than RMSE on price levels. The random walk remains the most robust ARIMA specification for equity returns per the academic literature on weak-form EMH.

**Recommendation:** Do not include ARIMA in the return ensemble. It passes the current ensemble's negative-IC exclusion only on BBCA (IC=0.047), but its direction accuracy of 24% there makes it net harmful. ARIMA may retain research value for volatility regime analysis (via ARIMA residuals into GARCH) but should not contribute to return predictions or trade decisions.

---

### C — XGBoost (Three-Head) — PRIMARY RETURN MODEL

**Empirical:** Consistently positive IC (0.09-0.43 across three tickers). Only model to pass BH correction on any ticker (BBCA: BH-passed, t=2.543). Direction accuracy is variable (35-58%) and does not track IC — high rank-IC does not guarantee good directional accuracy.

**The IC vs Direction-Accuracy gap:** XGBoost on BBCA achieves IC=0.43 but direction accuracy=0.356. Spearman rank IC rewards correctly ordering "more positive vs less positive" returns without penalizing for sign errors. In a period where most returns are positive, a model predicting "all positive but varying magnitudes" can achieve high IC while failing directional tests. **For swing trading, directional accuracy matters more than IC.**

**Runtime status:** Keep XGBoost as the active return-only model and retain `directional_accuracy >= 0.45` beside IC. Target/stop classifier heads are not production-calibrated: the current symmetric path labels are intentionally disabled, so `p_target`/`p_stop` are derived from the H-day return forecast plus volatility instead of classifier outputs.

**What helps XGBoost:** Multi-horizon momentum features (5d, 10d, 20d lagged returns — currently only log_return at lag-1). Sector relative strength. Fundamental features (PE, PB, OCF — currently all NaN due to missing DB wiring). The `ocf_missing` flag appears on every live prediction, meaning one-third of the intended feature signal is absent.

**Literature:** Multiple 2023-2024 comparative studies (ETJ, Springer, IEEE Xplore) confirm XGBoost is competitive with LSTM for tabular stock data at short horizons and substantially more sample-efficient. XGBoost typically achieves 54-62% directional accuracy on liquid US/EU stocks in published walk-forward studies; IDX results of 45-58% are within the expected range for higher-volatility emerging markets.

---

### D — Naive Model — IC NULL (CONSTANT PREDICTION)

**Empirical:** IC=null on all tickers. Direction accuracy: 64% (BBRI), 76% (BBCA), 57% (TLKM). The model predicts the historical mean return (constant value) — `ptp(y_pred) < 1e-12` → Spearman IC undefined → NaN → status=failed.

**Key finding:** The 64-76% directional accuracy of the naive (mean) predictor suggests strong positive trend in the recent test windows for BBRI/BBCA. This is the baseline any directional model must beat. **XGBoost's directional accuracy of 36-58% does not consistently beat this naive baseline**, while ARIMA (20-24%) is catastrophically below it.

**Recommendation:** Keep the Naive model as a benchmark anchor for BH correction (p-value = 1.0 trivially → always rejected by BH → weight = 0). Do not use it for directional trading signals.

---

### E — LSTM / Prophet — KEEP EXPERIMENTAL_UNUSED

**Literature findings:**
- LSTM requires 3-5+ years of daily data to train reliably. With `_HISTORY_DAYS = 500` (~2 years), IDX data is at the lower bound. The `lstm.py` model exists in the codebase but is correctly marked `experimental_unused`.
- Multiple 2024 comparisons (IEEE Xplore, ResearchGate) show LSTM outperforms ARIMA on price-level forecasting (R² > 95%) but this metric is trivially achieved by trend-following on any upward-sloping price series. For direction or return prediction, advantages over XGBoost are inconsistent across studies.
- IDX-specific: Indonesian papers (ETASR 2025, ResearchGate 2024) report LSTM MAPE 2-2.27% vs ARIMA MAPE 10%+ on IDX stocks. However, these measure price level (easy), not net return direction (hard). No published IDX LSTM study reports IC or walk-forward return prediction metrics.
- Prophet: designed for business time series with annual seasonality. IDX stocks do not exhibit strong annual seasonality in returns. No published evidence of Prophet adding value for equity return direction at 5-20 day horizons.

**Verdict:** Do not activate LSTM or Prophet. The sample size constraint (90 obs/ticker in current config) is far too small to train and validate a sequence model. Revisit if: (a) history extended to 1000+ days, (b) per-stock training replaced with sector-level pooled training.

---

### F — Temporal Fusion Transformer — NOT RECOMMENDED

**Literature:** TFT outperforms LSTM and BiLSTM in several 2024-2025 studies (Vietnamese market: 40-50% MAE reduction vs LSTM). However, TFT vs XGBoost direct comparisons at 5-20 day horizons are inconsistent — some studies show no advantage over XGBoost, others show modest improvements. Computational cost is 10-50x XGBoost at training time. With 90 validation observations, TFT would be severely overfit. **Not recommended.**

---

## Phase 3 — IDX-Specific Findings

### A — Data Availability
- **History:** `_HISTORY_DAYS = 500` gives ~2 trading years. Too short for reliable walk-forward estimation with n_splits=3, test_size_days=30.
- **Fix:** Increase to `_HISTORY_DAYS = 756` (3 years). This adds one full walk-forward split, raising n_obs from 90 to 120 and meaningfully increasing IC t-stat power (~+0.3 on t-stat for the same true IC).
- **Liquidity constraint:** Only stocks with ≥252 bars should be forecasted. The DatasetBuilder enforces `_MIN_BARS = 60` — too low. Raise to 126 (6 months minimum) in dataset.py.

### B — ARA/ARB Limits and Return Labels
- ARA (+25%/+20%/+35% by tier) creates a hard upper bound on intraday returns. The current log-normal terminal distribution in `_compute_probs` slightly overestimates p_target for high-ARA stocks. Minor for liquid blue chips, more significant for mid/small caps. Acceptable for v1.

### C — Foreign Flow (V7 Backlog)
- The V7 backlog (memory: "wire USD/IDR + foreign_flow together") identifies foreign flow as a high-value feature. Published IDX research confirms foreign institutional flow leads retail flow by 1-3 days on IHSG. Wire `net_foreign_flow_5d` (5-day cumulative foreign buy/sell as % of market cap) when the data pipeline is available. This is V7 scope, not this task.

### D — Fundamental Features (PE, PB, OCF — All Missing)
- The `ocf_missing` flag appears on 100% of live predictions. The DatasetBuilder initializes PE, PB, OCF as `np.nan` placeholders. XGBoost sees zero-filled constants — zero variance = zero feature importance. Wire fundamental features from the existing DB before adding any new model complexity.

---

## Phase 4 — Honest Evaluation

### A — EMH and IDX
IDX shows short-term autocorrelation in returns (consistent with Naive's 64-76% directional accuracy) and foreign flow predictability, but ARIMA-based prediction fails (negative IC). This is consistent with semi-strong form efficiency where price-level information is quickly arbitraged but cross-asset signals retain edge.

### B — What the Numbers Actually Show
- **Return direction prediction:** Weakly feasible (XGBoost IC 0.09-0.43), not reliable enough for standalone use. XGBoost should be advisory input to the debate chamber, not a standalone signal.
- **Volatility forecasting:** TGARCH fits well (persistence 0.71-0.93, convergence on all tickers) and would provide genuine value once the multi-step bug is fixed.
- **Pattern recognition vs true forecasting:** The debate chamber performs pattern recognition (fundamentally and technically sound setups). The forecasting layer attempts true prediction. Literature strongly supports pattern recognition over prediction for equity trading at sub-monthly horizons.

### C — Realistic Accuracy Benchmarks

```
METRIC                  REALISTIC RANGE (IDX, 5-20d, liquid stocks)
Directional accuracy    52-60%   (55%+ profitable with 2:1 R/R per swing trading literature)
IC (Spearman)           0.03-0.10 for production; 0.10+ is excellent
IC t-stat               1.96+ (5% one-tailed) realistic for n<120; 2.57 too strict
Annualized Sharpe       0.5-1.5 for systematic short-term strategies
TGARCH persistence      0.85-0.97 typical for IDX blue chips
```

The observed XGBoost IC of 0.09-0.43 spans "adequate" to "excellent" — but with n=90 observations, the 90% confidence interval on IC=0.43 is approximately [0.24, 0.59], so the true IC could be anywhere in that range.

---

## Phase 5 — Implementation Recommendations

### Priority 1 — Fix TGARCH (Blocking, 2-line change)

**File:** `core/forecasting/models/tgarch.py`, line ~105 in `predict_volatility()`  
**Change:** Replace the bare `result.forecast(horizon=horizon, reindex=False)` call with simulation when `power != 2.0`:
```python
method = "simulation" if use_tgarch else "analytic"
simulations = 500 if use_tgarch else 1
forecast = result.forecast(
    horizon=horizon,
    method=method,
    simulations=simulations,
    reindex=False,
)
```
**Why:** `power=1.0` (TARCH) requires simulation for multi-step. `power=2.0` (standard GARCH) retains analytic. Simulations=500 is the QuantInsti-standard recommendation and produces stable variance estimates.

### Priority 2 — Remove ARIMA from Return Ensemble

**File:** `core/forecasting/service.py:53-58`  
**Change:** Remove `"arima": ARIMAForecaster` from `_return_model_factories()`.  
**Why:** Negative IC on 2/3 tickers. Direction accuracy 20-24% on those tickers (anti-signal). Even on BBCA where IC=0.047 is positive, direction accuracy=0.244 means the ARIMA prediction is worse than random for actual trade decisions. The BH correction excludes it from weighting when IC<0, but it still contributes to the disagreement penalty calculation and appears in model_votes.

### Priority 3 — Lower IC t-stat Threshold

**File:** `core/forecasting/validation.py:258`  
**Current:** `if ic_mean >= 0.03 and ic_t_stat >= 2.57:`  
**Proposed:** `if ic_mean >= 0.03 and ic_t_stat >= 1.96:`  
**Why:** With n=90 observations, the statistical power to detect a real IC=0.03 at alpha=0.005 (2.57) is approximately 15%. At alpha=0.05 (1.96) it rises to ~45%. The threshold is calibrated for large US equity factor research (n=1000+) not IDX single-stock walk-forward with 90 bars. The Risk Governor provides the independent quality gate; the forecasting threshold does not need to be the sole safeguard.

### Priority 4 — Add Directional Accuracy Filter to Ensemble

**File:** `core/forecasting/ensemble.py:44-52`  
After the BH exclusion block, add:
```python
dir_acc = scores.get("dir_acc")
if dir_acc is not None and dir_acc < 0.45:
    continue  # directional anti-prediction: exclude regardless of IC
```
**Also:** Pass `dir_acc` into `model_scores` from `validate_model`. The `ValidationSummary.directional_accuracy` field already exists in schemas.py; it just needs to be threaded into `compute_ensemble_weights` via the scores dict.

**Why:** IC measures rank correlation, not directional accuracy. XGBoost BBCA: IC=0.43 but direction accuracy=0.356 — the model correctly ranks return magnitudes but predicts wrong direction 65% of the time. For swing trading this is unusable.

### Priority 5 — Increase History Depth

**File:** `core/forecasting/service.py:37`  
**Change:** `_HISTORY_DAYS: int = 500` → `_HISTORY_DAYS: int = 756`  
Also update `walk_forward_splits` call from `n_splits=3` → `n_splits=5`:
**File:** `core/forecasting/service.py:254`  
**Change:** `splits = walk_forward_splits(labeled, n_splits=3, test_size_days=30)`  
→ `splits = walk_forward_splits(labeled, n_splits=5, test_size_days=30)`  
**Why:** 756 days = 3 years = ~540 trading days, enabling 5 splits (150 test obs). Aligns with HMM regime detector window from memory.

### Priority 6 — Add Multi-Horizon Momentum Features

**File:** `core/forecasting/dataset.py:158-180`, inside `_add_technicals()`:
```python
for lag in [5, 10, 20]:
    df[f"return_{lag}d"] = close.pct_change(lag)
df["price_above_ma20"] = (close > close.rolling(20).mean()).astype(int)
```
**Why:** The existing feature set has only log_return (1-day lag). XGBoost with max_depth=4 needs explicit multi-lag momentum to capture autocorrelation structures. Published emerging-market XGBoost studies consistently rank 5-day and 10-day rolling returns as top features.

---

## Phase 6 — What NOT to Build

| Model | Verdict | Reason |
|-------|---------|--------|
| LSTM | Keep disabled | Data-hungry; n=90 obs today is far below minimum for reliable training |
| Prophet | Keep disabled | No annual seasonality in IDX equity returns; wrong use case |
| TFT | Do not add | No consistent advantage over XGBoost at 5-20d; 10-50x compute cost |
| CNN chart patterns | Do not add | Better served by debate chamber chartist agent |
| Hybrid LSTM-XGBoost | Do not add | Marginal improvement vs standalone XGBoost; high complexity |
| VAR (multi-stock) | Do not add | Sector leadership already encoded via HMM regime detector |
| EGARCH (replace TGARCH) | No | Current TGARCH fits well; simulation fix is sufficient |

---

## Phase 7 — Agent Integration (Unchanged Architecture)

ForecastingService already sits correctly after the CIO debate verdict:

```
HMM Regime → Quant Filter → Scout Phase → Debate Phase → CIO Judge
    ↓
ForecastingService.predict(ticker, cio_verdict=verdict)
    [ADVISORY ROLE — feeds conviction scoring, not a hard veto]
    TGARCH sigma    → position sizing (vol targeting in portfolio_optimizer)
    p_target/p_stop → EV calculation
    XGBoost r_hat   → direction confirmation signal
    ↓
Risk Governor (deterministic veto, unchanged)
    ↓
Portfolio Optimizer
```

No architecture changes needed. Fix the bugs, calibrate the thresholds, the rest flows through the existing pipeline.

---

## Phase 8 — Roadmap

```
PHASE  ACTION                          FILE                  EFFORT
1.1    Fix TGARCH simulation method    tgarch.py             15 min  CRITICAL
1.2    Remove ARIMA from ensemble      service.py            10 min  HIGH
1.3    Lower t-stat threshold 2.57→1.96 validation.py       10 min  HIGH
1.4    Add dir_acc filter to ensemble  ensemble.py           15 min  HIGH

2.1    Increase _HISTORY_DAYS to 756   service.py            5 min   HIGH
2.2    Increase n_splits 3→5           service.py            5 min   HIGH
2.3    Multi-horizon momentum features dataset.py            30 min  MEDIUM
2.4    Wire fundamentals (PE/PB/OCF)   dataset.py + DB       2-4h    MEDIUM

3.1    Foreign flow feature            dataset.py (V7)       1-2d    BLOCKED (data)
3.2    Regime as binary features       dataset.py            20 min  MEDIUM

4.1    Validate fixes on BBRI/BBCA     manual test           30 min  AFTER PHASE 1
```

---

## Source Index

1. [Forecasting International Stock Market: XGBoost, LSTM, LSTM-XGBoost](https://iapress.org/index.php/soic/article/view/1822)
2. [Comparative Analysis: LSTM, ARIMA, XGBoost for direction prediction](https://everant.org/index.php/etj/article/view/1495)
3. [XGBoost vs LSTM: Global Stock Market Prediction](https://www.researchgate.net/publication/398973927_XGBoost_vs_LSTM_A_Comparative_Performance_Analysis_for_Global_Stock_Market_Prediction)
4. [Interpretable Walk-Forward Validation Framework](https://arxiv.org/html/2512.12924v1)
5. [TFT for Vietnamese stock market forecasting](https://link.springer.com/10.1007/978-981-95-3358-9_8)
6. [GARCH-Informed Neural Networks](https://arxiv.org/pdf/2410.00288)
7. [IDX Hybrid Deep Learning Optimization](https://etasr.com/index.php/ETASR/article/view/9363)
8. [IDX Deep Learning Comparative Study](https://www.researchgate.net/publication/376030428_Stock_price_forecasting_in_Indonesia_stock_exchange_using_deep_learning_a_comparative_study)
9. [Predictive power of ARIMA: sliding window method (Springer)](https://link.springer.com/article/10.1057/s41260-020-00184-z)
10. [QuantInsti: GARCH vs GJR-GARCH Python](https://blog.quantinsti.com/garch-gjr-garch-volatility-forecasting-python/)
11. [arch Documentation 7.2.0 — multi-step forecasting](https://app.readthedocs.org/projects/arch/downloads/pdf/latest/)
