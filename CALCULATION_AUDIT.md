# Calculation Audit — IDX Fundamental Analysis Engine

**Audit date:** 2026-06-22  
**Auditor:** Claude Sonnet 4.6 (max-effort)  
**Scope:** Every calculation in the codebase — mathematical correctness AND IDX-specific calibration  
**Files covered:** `utils/technicals.py`, `utils/trade_math.py`, `core/quant_filter/pipeline.py`, `core/quant_filter/position_sizer.py`, `core/quant_filter/config.py`, `services/fair_value_calculator.py`, `core/risk_governor.py`, `core/regime.py`, `core/backtester/metrics_calculator.py`

---

## FINDINGS DASHBOARD

| # | Calculation | File | Status | Severity |
|---|-------------|------|--------|----------|
| 1 | ATR — Wilder's vs SMA | `utils/technicals.py:55` | WRONG FORMULA | HIGH |
| 2 | Sharpe — annualization factor | `core/backtester/metrics_calculator.py:111` | WRONG FORMULA | HIGH |
| 3 | ARB threshold vs current BEI rule | `core/quant_filter/pipeline.py:185` | UNVERIFIED REGULATORY VALUE | HIGH |
| 4 | Target price not bounded by ARA limit | `services/fair_value_calculator.py` | MISSING CALCULATION | HIGH |
| 5 | T+2 settlement not modelled | `core/quant_filter/position_sizer.py` | MISSING CALCULATION | MEDIUM |
| 6 | R/R thresholds ignore round-trip costs | `utils/trade_math.py:48` | IDX MISCALIBRATION | MEDIUM |
| 7 | Bollinger Bands — sample vs population std | `utils/technicals.py:215` | SPEC DEVIATION | MEDIUM |
| 8 | Volume surge self-reference | `core/quant_filter/pipeline.py:636` | SYSTEMATIC BIAS | MEDIUM |
| 9 | Graham k=18.2 is sector-agnostic | `core/quant_filter/config.py` | IDX CALIBRATION TRADE-OFF | LOW |
| 10 | Risk budget is not per-position | `core/quant_filter/position_sizer.py:364` | RISK MANAGEMENT | LOW |
| 11 | DDM undervalues high-ROE compounders | `services/fair_value_calculator.py:869` | MODEL LIMITATION | LOW |
| 12 | MACD histogram — zero edge case | `utils/technicals.py:128` | MINOR CLASSIFICATION | LOW |
| 13 | RSI — Wilder's EWM | `utils/technicals.py:31` | CORRECT | — |
| 14 | MACD — 12/26/9 standard EWM | `utils/technicals.py:99` | CORRECT | — |
| 15 | snap_to_tick — IDX tick fractions | `utils/technicals.py:61` | CORRECT | — |
| 16 | VWAP rolling 20-day | `utils/technicals.py:424` | CORRECT | — |
| 17 | Anchored VWAP from lowest low | `utils/technicals.py:471` | CORRECT | — |
| 18 | Fibonacci retracement levels | `utils/technicals.py:551` | CORRECT | — |
| 19 | 52-week range signal (NEAR_HIGH >= 80%) | `utils/technicals.py:904` | CORRECT | — |
| 20 | Gap detection (GAP_UP/DOWN vs prior H/L) | `utils/technicals.py:329` | CORRECT | — |
| 21 | R/R formula reward/risk direction | `utils/trade_math.py:48` | CORRECT | — |
| 22 | Graham Number formula sqrt(k x EPS x BVPS) | `core/quant_filter/pipeline.py:1310` | CORRECT (k choice: see #9) | — |
| 23 | PE-based FV = EPS x hist_PE | `services/fair_value_calculator.py:837` | CORRECT | — |
| 24 | PB-based FV with ROE/Ke cap | `services/fair_value_calculator.py:849` | CORRECT | — |
| 25 | DDM guard (ke - g < 0.03 -> None) | `services/fair_value_calculator.py:877` | CORRECT | — |
| 26 | Composite FV — weight normalization | `services/fair_value_calculator.py:961` | CORRECT | — |
| 27 | SOE 15% governance discount | `services/fair_value_calculator.py` | CORRECT — within 10-20% range | — |
| 28 | Margin of Safety = (FV - P) / P x 100 | `services/fair_value_calculator.py:1041` | CORRECT | — |
| 29 | CAPM Ke = SBN10Y + beta x IDX_ERP | `services/fair_value_calculator.py:239` | CORRECT | — |
| 30 | EV/EBITDA FV (mining only, 5.5x target) | `services/fair_value_calculator.py:895` | CORRECT | — |
| 31 | Realized volatility (regime) 20-day | `core/regime.py:76` | CORRECT | — |
| 32 | Regime thresholds (HIGH >= 2%, LOW < 1%) | `core/regime.py` | IDX-ALIGNED | — |
| 33 | DEFENSIVE (weekly <= -5% OR below MA20/50/200) | `core/regime.py` | CORRECT | — |
| 34 | Weekly return = 5 trading-day return | `core/regime.py:135` | CORRECT | — |
| 35 | Stop loss: max(SMA20-based, ATR-based, 88% floor) | `core/quant_filter/pipeline.py:798` | CORRECT (inherits ATR bug) | — |
| 36 | Position sizing: lot_from_risk and lot_from_alloc | `core/quant_filter/position_sizer.py:363` | CORRECT | — |
| 37 | Lot floor division (floor to nearest 100 shares) | `core/quant_filter/position_sizer.py:365` | CORRECT | — |
| 38 | Transaction costs 0.15% buy + 0.35% sell | `core/quant_filter/position_sizer.py` | ACCEPTABLE APPROXIMATION | — |
| 39 | ADT 20d = (close x vol).tail(20).mean() | `core/quant_filter/pipeline.py:624` | CORRECT (see note #8) | — |
| 40 | EMA20, MA200 parameters | `core/quant_filter/pipeline.py:521` | CORRECT | — |
| 41 | RS vs IHSG = stock 1m return - IHSG 1m return | `core/quant_filter/pipeline.py:570` | CORRECT | — |
| 42 | ARA HIGH > +20% (fires before +25% ARA limit) | `core/quant_filter/pipeline.py:194` | CONSERVATIVE — IDX-ALIGNED | — |
| 43 | Val_Score: PBV-sector blend (financial) | `core/quant_filter/pipeline.py:942` | CORRECT | — |
| 44 | Val_Score: Graham gap 70% + PE-sector 30% (non-fin) | `core/quant_filter/pipeline.py:942` | CORRECT | — |
| 45 | Win rate = closed_wins / total_closed | `core/backtester/metrics_calculator.py:58` | CORRECT | — |
| 46 | RR_IMPLAUSIBLE_CEILING = 5.0 | `core/risk_governor.py` | IDX-ALIGNED | — |
| 47 | ADT hard-reject < Rp 2B, soft-flag < Rp 10B | `core/risk_governor.py` | IDX-ALIGNED | — |
| 48 | Circuit-breaker at -3% daily portfolio loss | `core/risk_governor.py` | IDX-ALIGNED | — |
| 49 | Counter-trend R/R floor = 2.5x | `core/risk_governor.py` | IDX-ALIGNED | — |
| 50 | Staleness: >30 days shifts 50% weight to P/B | `services/fair_value_calculator.py` | SENSIBLE | — |
| 51 | EV/EBITDA FV: price x (5.5 / current_ev_ebitda) | `services/fair_value_calculator.py:895` | CORRECT | — |
| 52 | MACD: fast=12, slow=26, signal=9 (standard EWM) | `utils/technicals.py:99` | CORRECT | — |
| 53 | Regime ATR stop multiplier 2.5x (NORMAL/LOW/HIGH) | `utils/technicals.py` | IDX-ALIGNED | — |
| 54 | Trailing stop multipliers (1.5x LOW, 2.5x DEFENSIVE) | `utils/trade_math.py:326` | IDX-ALIGNED | — |
| 55 | Target deployment pct clamped [0.40, 0.70] | `core/quant_filter/position_sizer.py` | CORRECT | — |

---

## SECTION 1 — TECHNICAL INDICATORS

### Finding #1: ATR uses SMA instead of Wilder's smoothing

**Location:** `utils/technicals.py:55`  
**Status: WRONG FORMULA — HIGH severity**

**Current code:**
```python
def compute_atr(high, low, close, window=14):
    tr = pd.concat([high - low,
                    (high - close.shift(1)).abs(),
                    (low  - close.shift(1)).abs()], axis=1).max(axis=1)
    return tr.rolling(window).mean()   # SMA — wrong
```

**Correct formula (Wilder's smoothing, same method as compute_rsi):**
```python
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
```

ATR was defined by Wilder (1978) with: `ATRn = (ATRn-1 x (N-1) + TRn) / N`, equivalent to EWM with `alpha=1/N, adjust=False`. The codebase already uses this method correctly for RSI (`utils/technicals.py:41`) but uses a simple rolling mean for ATR — an inconsistency.

**Verified numerical impact — TLKM-like stock (Rp 3,200, spike day TR = 450, normal day TR ~30):**

| Day after spike | ATR (SMA, current) | ATR (Wilder's, correct) | Difference |
|---|---|---|---|
| +1 | Rp 64.5 | Rp 57.4 | +Rp 7.1 (12% too wide) |
| +2 | Rp 64.6 | Rp 55.3 | +Rp 9.4 (17% too wide) |
| +3 | Rp 62.9 | Rp 52.9 | +Rp 10.0 (19% too wide) |
| +5 | Rp 65.2 | Rp 52.0 | +Rp 13.2 (25% too wide) |
| +7 | Rp 64.5 | Rp 50.2 | +Rp 14.4 (29% too wide) |

**Stop-loss impact (entry Rp 3,300, NORMAL regime 2.5x multiplier):**

| Day after spike | Stop (SMA, current) | Stop (Wilder's, correct) | Difference |
|---|---|---|---|
| +1 | Rp 3,139 | Rp 3,157 | -Rp 18 per share / -Rp 1,775 per lot |
| +3 | Rp 3,143 | Rp 3,168 | -Rp 25 per share / -Rp 2,500 per lot |
| +5 | Rp 3,137 | Rp 3,170 | -Rp 33 per share / -Rp 3,296 per lot |

**Root cause of divergence:** With SMA, the spike's True Range stays in the 14-bar window for 14 full days. With Wilder's EWM, the spike decays exponentially — by day 5 it has only 69% of its original weight. SMA produces stops that are persistently too wide for entries made after a volatility spike, making R/R look worse than it actually is.

**Fix (one line):**
```python
# utils/technicals.py:55
# OLD:
    return tr.rolling(window).mean()
# NEW:
    return tr.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
```

---

### Finding #7: Bollinger Bands — sample std (ddof=1) instead of population std (ddof=0)

**Location:** `utils/technicals.py:215`  
**Status: SPEC DEVIATION — MEDIUM severity**

**Current code:**
```python
rolling_std = close.rolling(period).std()   # pandas default: ddof=1 (sample std)
```

Bollinger's original specification (1983) defines bands using population standard deviation (divided by N). Pandas `.rolling().std()` defaults to sample std (divided by N-1, `ddof=1`).

**Verified numerical impact — BBCA-like prices (N=20, SMA = Rp 9,674):**

| Method | Std value | BB Upper | BB Lower |
|---|---|---|---|
| Sample ddof=1 (current code) | Rp 66.38 | Rp 9,806.9 | Rp 9,541.1 |
| Population ddof=0 (correct) | Rp 64.70 | Rp 9,803.5 | Rp 9,544.5 |
| Difference | Rp 1.68 | Rp 3.4 wider | Rp 3.4 wider |

The ratio is exactly sqrt(20/19) = 1.0260 as expected — sample std is 2.6% wider. The absolute Rp 3.4 difference on a Rp 9,700 stock is small, but it creates **systematic bias in band-width squeeze detection** — the code's squeeze threshold (20th-percentile bandwidth) is calibrated against a consistently wider bandwidth, so squeezes fire later than they should.

**Fix (one word):**
```python
# utils/technicals.py:215
# OLD:
    rolling_std = close.rolling(period).std()
# NEW:
    rolling_std = close.rolling(period).std(ddof=0)
```

---

### Finding #8: Volume surge ratio includes today in its own average

**Location:** `core/quant_filter/pipeline.py:636`  
**Status: SYSTEMATIC BIAS — MEDIUM severity**

**Current code:**
```python
vol_20d_avg = float(vol.tail(20).mean())    # includes today
curr_vol    = float(vol.iloc[-1])           # also today
vol_surge_ratio = curr_vol / vol_20d_avg   # self-referential
```

When today's volume is an extreme outlier, it inflates the denominator and understates the surge signal.

**Verified numerical impact — ASII large-news day:**

| Metric | Current code | Correct (prior 20d only) |
|---|---|---|
| 20d avg used | 55,000,000 (includes today's 150M) | 50,000,000 |
| Surge ratio | 2.73x | 3.00x |
| Understatement | 9.1% | — |

For a borderline setup at the 2.0x tier1 threshold: a true 2.10x surge is reported as 1.91x — misclassified to tier2, reducing the momentum score and potentially dropping the stock from the top 10.

**Fix:**
```python
# core/quant_filter/pipeline.py ~line 635
# OLD:
vol_20d_avg = float(vol.tail(20).mean())
# NEW:
vol_20d_avg = float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else float(vol.iloc[:-1].mean())
```

---

### Finding #12: MACD histogram zero edge case

**Location:** `utils/technicals.py:128`  
**Status: MINOR CLASSIFICATION — LOW severity**

The `else` branch catches `hist_now == 0` (exact zero) as `NEGATIVE_SHRINKING`. With floating-point prices this is extremely rare, but a zero histogram value is neither positive nor negative. Not worth a dedicated fix unless false classification is observed in logs.

---

## SECTION 2 — RISK & MONEY MANAGEMENT

### Finding #6: R/R thresholds do not account for round-trip transaction costs

**Location:** `utils/trade_math.py:48`, `core/risk_governor.py`  
**Status: IDX MISCALIBRATION — MEDIUM severity**

IDX round-trip transaction cost: 0.15% buy + 0.25% sell + 0.10% PPh Final = **0.50% total**.

The R/R minimum thresholds (1.3x large-cap, 1.5x default) are applied to **nominal pre-cost R/R**. After the 0.50% round trip, actual net R/R is lower.

**Concrete example — BBCA mid-cap entry Rp 9,500:**

| | Nominal | After 0.50% round-trip costs |
|---|---|---|
| Stop Rp 9,025 (risk Rp 475) | R/R = 1.95x | Effective risk = Rp 489 (+Rp 14 buy cost) |
| Target Rp 10,425 (reward Rp 925) | | Effective reward = Rp 889 (-Rp 36 sell cost) |
| Net R/R | **1.95x** | **1.82x** |

For a setup exactly at the 1.50x threshold:
- Nominal R/R 1.50x → net R/R after costs = **1.38x**
- That is 8% below the stated minimum in real economic terms

**Recommended fix:** Adjust minimum thresholds upward by ~0.10 to ensure net R/R meets the stated floor:
- Large-cap: 1.3 → 1.4
- Default: 1.5 → 1.62 (or round to 1.65 for clarity)

---

### Finding #10: Risk budget is portfolio-level, not per-position

**Location:** `core/quant_filter/position_sizer.py:364`  
**Status: RISK MANAGEMENT — LOW severity**

```python
# max_loss_budget = total_capital x max_loss_pct  (computed ONCE)
lot_from_risk = floor(max_loss_budget / (item["risk_per_share"] * LOT_SIZE))
```

Each position is sized against the **full** `max_loss_budget`. With 3 positions, total portfolio risk exposure can reach 3x `max_loss_pct`. The allocation cap partially limits this but does not fully enforce it — a position with a tight stop and high allocation could still exhaust the full budget independently.

**Fix:**
```python
per_position_budget = max_loss_budget / max_positions
lot_from_risk = floor(per_position_budget / (item["risk_per_share"] * LOT_SIZE))
```

---

### Finding #4 (MISSING): Target price not validated against ARA limit

**Location:** Absent from `services/fair_value_calculator.py` and `core/risk_governor.py`  
**Status: MISSING CALCULATION — HIGH severity**

The CIO Verdict sets `target_price`. No code verifies that `(target_price - entry) / entry <= ARA_for_this_tier`. IDX ARA limits (post-April 2025):
- Price < Rp 200: +35%
- Price Rp 200–5,000: +25%
- Price > Rp 5,000: +20%

**Example:** A Rp 3,000 stock with a CIO target of Rp 4,050 implies a +35% move — physically unreachable in one session (ARA = +25%). The trade is still achievable across 2 sessions, but the risk governor should flag this so the user can set a session-realistic stop-and-re-evaluate point.

**Recommended addition in `core/risk_governor.py`:**
```python
def _ara_sessions_needed(entry: float, target: float) -> int:
    """How many consecutive ARA sessions to reach target."""
    ara = 0.35 if entry < 200 else (0.25 if entry <= 5000 else 0.20)
    if target <= entry:
        return 0
    import math
    return math.ceil(math.log(target / entry) / math.log(1 + ara))
```
Flag (not hard-reject) if `sessions_needed > swing_horizon_days`.

---

### Finding #5 (MISSING): T+2 settlement not modelled

**Location:** Absent from `core/quant_filter/position_sizer.py`  
**Status: MISSING CALCULATION — MEDIUM severity**

IDX settlement is T+2. Cash from a Monday sale is not available until Wednesday. The position sizer does not reduce `available_capital` for trades pending settlement, which can cause capital double-counting when two BUY signals fire within the same 2-day window.

Impact is limited to same-week multi-trade recycling. For a 3+ day swing strategy with infrequent trading, this rarely matters. Recommend implementing as a soft flag rather than a hard constraint.

---

## SECTION 3 — FAIR VALUE CALCULATIONS

### Finding #9: Graham k=18.2 is sector-agnostic

**Location:** `core/quant_filter/config.py`  
**Status: IDX CALIBRATION TRADE-OFF — LOW severity**

k=18.2 (= 13.0 P/E x 1.4 P/B) is a deliberate IDX calibration vs the standard k=22.5.

**Verified numerical impact — ASII (EPS=400, BVPS=5,000, Price=5,800):**

| Constant | Graham FV | Gap vs price |
|---|---|---|
| Standard k=22.5 | Rp 6,708 | +15.7% |
| IDX k=18.2 | Rp 6,033 | +4.0% |

**Val_Score tier impact (undervalued scenario, EPS=300, BVPS=3500, Price=4200):**
- k=22.5: gap = +15.7% → tier2 (gap >= 15%)
- k=18.2: gap = +4.1% → tier3 (gap < 15%)

This shifts composite score by ~6 points. k=18.2 is appropriate for the broad IDX universe (average P/E 12–14x) but is conservative for consumer staples and telecom with sustained above-average growth. No code change needed unless sector-specific Graham k values are implemented.

---

### Finding #11: DDM structurally undervalues high-ROE compounders (model limitation)

**Location:** `services/fair_value_calculator.py:869`  
**Status: MODEL LIMITATION — LOW severity**

**Example — BBCA (DPS Rp 340, Ke ~15% from CAPM, g = 7%):**
```
DDM FV = 340 / (0.15 - 0.07) = Rp 4,250   vs   market Rp 9,500
```

The 5% bank sector weight limits the damage to ~Rp 250 FV drag — the code's architecture already correctly de-weights DDM for banks. However, for stocks where Ke persistently exceeds 14%, suppressing DDM (similar to the `ke - g < 0.03` guard) would prevent a systematically low anchor from polluting the composite FV.

---

## SECTION 4 — IDX-SPECIFIC CALCULATIONS

### Finding #3: ARB threshold — BEI rule conflict between code comment and project context

**Location:** `core/quant_filter/pipeline.py:185`  
**Status: UNVERIFIED REGULATORY VALUE — HIGH severity**

| Source | ARB Limit | Code threshold |
|---|---|---|
| Code comment (`pipeline.py:171`) | -15% flat (post-April 2025) | HIGH at -12%, MEDIUM at -7% |
| Project context notes | -7% flat (post-2024) | — |

These two values directly contradict each other. If the true ARB is -7%, then:
- The HIGH threshold of -12% is **unreachable** (stocks cannot drop 12% before the -7% ARB halts trading)
- The MEDIUM threshold of -7% fires exactly at the limit (no early warning)
- Every stock susceptible to a full ARB stop would show as MEDIUM, never HIGH

If the true ARB is -15%, the thresholds are sensible (HIGH fires before the limit as an early warning).

**Action required:** Verify the current ARB limit against the official BEI Surat Edaran. Then update the thresholds to approximately 80-85% of the actual limit for HIGH, 50% for MEDIUM:
- If ARB = -7%: HIGH at -6.0%, MEDIUM at -4.0%
- If ARB = -15%: HIGH at -12% (current is correct), MEDIUM at -7% (current is correct)

---

## SECTION 5 — BACKTESTER METRICS

### Finding #2: Sharpe ratio — sqrt(252) annualization is wrong for per-trade returns

**Location:** `core/backtester/metrics_calculator.py:111`  
**Status: WRONG FORMULA — HIGH severity**

**Current code:**
```python
return (mean / std) * math.sqrt(252)   # treats per-trade returns as daily
```

**The error:** `sqrt(252)` converts daily return Sharpe to annual. Per-trade swing returns are NOT daily — they span 3–15 days. Applying `sqrt(252)` to per-trade returns implicitly assumes 252 trades per year, each lasting exactly 1 day.

**Verified numerical impact (mean = 3.5%, std = 4.2%, IDX swing avg hold H days):**

| Formula | Result | Notes |
|---|---|---|
| Current: IR x sqrt(252) | **13.2** | Wrong |
| Correct: IR x sqrt(252/7) — H=7d | **5.0** | Realistic excellent |
| Correct: IR x sqrt(252/10) — H=10d | **4.2** | Realistic good |

The current code **overstates Sharpe by 2.6x to 3.2x** vs realistic IDX swing holding periods. The code comment acknowledges this is an approximation but does not quantify the magnitude of overstatement. A reported Sharpe of 13 suggests a world-class quant fund; the true value of 4–5 is excellent but achievable.

**Fix (requires tracking holding days per trade, or use a fixed conservative assumption):**
```python
# Option A — if trade objects have holding_days attribute:
avg_hold = sum(t.holding_days for t in closed_trades) / len(closed_trades)
return (mean / std) * math.sqrt(252 / max(avg_hold, 1))

# Option B — fixed IDX swing assumption (conservative):
IDX_SWING_AVG_HOLD_DAYS = 10
return (mean / std) * math.sqrt(252 / IDX_SWING_AVG_HOLD_DAYS)
```

---

## PRIORITIZED CORRECTION LIST

Ordered by **impact x ease of fix**:

| Priority | Finding | Why First | Effort |
|---|---|---|---|
| P1 | ATR: SMA -> Wilder's EWM | Stop placement wrong after every spike; all stop-based exits inherit error | 1 line |
| P2 | ARB: verify BEI rule and correct thresholds | Regulatory compliance; HIGH classification may be unreachable or always firing | Research + 2 lines |
| P3 | Sharpe: fix annualization factor | Backtest Sharpe 2.6-3.2x overstated; misleads strategy evaluation | 3 lines |
| P4 | Target price: add ARA sessions check | Trades with multi-session targets not flagged; affects execution planning | ~15 lines |
| P5 | Bollinger: ddof=0 | 2.6% wider bands; squeeze detection bias; fixes TradingView alignment | 1 word |
| P6 | Volume surge: exclude today | 9.1% understatement; borderline tier1 signals misclassified | 1 line |
| P7 | R/R thresholds: cost-adjust | 1.5x threshold is really 1.38x net; stated floor understates required gross R/R | Config change |
| P8 | T+2: track pending settlement | Capital double-count in same-week multi-trade recycling | ~20 lines |
| P9 | Risk budget: per-position not portfolio | Multiple positions can each consume full budget | 1 line |
| P10 | Graham k: note sector limitation | No code change needed — add comment documenting the trade-off | Documentation |
| P11 | DDM: suppress when Ke > 14% | Small FV drag already mitigated by 5% weight | 3 lines |
| P12 | MACD histogram zero edge case | Rare float edge case; low production impact | 1 line |
