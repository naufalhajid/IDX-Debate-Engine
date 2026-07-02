# Strategic Alignment Audit: IDX Swing Trading System

**Date:** 2026-06-21
**Scope:** R/R Threshold, Target Price, Fair Value Threshold, Risk Governor
**Question:** If this code ran perfectly with zero bugs, would it behave like a competent swing trader — or would it be confidently wrong?

---

## R/R THRESHOLD — Conceptual Alignment

**What the code actually does:**
- Calculates R/R from `entry_high` as worst-case fill: `reward = target − entry_high`, `risk = entry_high − stop`
- Tiered floors: 1.3x for large-cap (≥Rp 50T), 1.5x default
- Hard ceiling: 5.0x is rejected as "implausible geometry"
- Binary gate: below minimum = hard reject

**Assessment:**

Using `entry_high` as the worst-case fill is textbook professional trading — you price every trade at its worst possible fill, never the midpoint. The tiered thresholds by market cap are also defensible: liquid blue-chips (BBCA, BBRI) have tighter spreads and more predictable intraday behavior, so 1.3x is genuinely different in quality from 1.3x on a mid-cap.

The 5x ceiling is correct: R/R above 5x almost always means a broken stop (inside the noise band) or a fantasy target (pre-crash high that won't be revisited this swing).

**Where it fails the real-trading test:**

The R/R minimum is **flat across all market regimes and setup types.** In DEFENSIVE regime, gap risk is elevated — a 4% overnight gap can hit your stop before the trade even develops. A 1.3x R/R in DEFENSIVE regime doesn't compensate for that increased random-walk risk. The system adjusts ATR multipliers and stop distances for regime (correctly), but leaves R/R minimums unchanged.

More critically: **counter-trend setups (price below MA200) get the same R/R floor as trend-following setups.** Counter-trend trades have structurally lower win rates — practitioner experience consistently puts counter-trend win rates 15–20 percentage points below trend-following rates in the same market. For expected value to remain positive, you need meaningfully higher R/R. A real trader running counter-trend setups on IDX would require 2.5x minimum, not 1.5x. This system passes counter-trend setups at 1.6x R/R, which is expected-value negative at realistic win rates.

**Verdict: Partially aligned**

The mechanics are correct (worst-case fill, tiered by market cap, implausibility ceiling). The flat threshold across regime and setup type misses the real-world nuance that makes R/R floors meaningful.

---

## TARGET PRICE — Conceptual Alignment

**What the code actually does** (`_compute_trade_envelope`, debate_chamber.py lines 3763–3809):

1. **Seed**: `target = entry_high + (stop_risk × 2.0)` — start at 2.0x R/R mathematically
2. **Floor**: minimum 4% gain from entry_mid
3. **Resistance bump**: if 20d high ≥ seed → use 20d high; elif 50d high ≥ seed → use 50d high; elif 52w high ≤ 1.30× current price → use 52w high
4. **FV ceiling**: `if target > fair_value → target = fair_value` ← critical issue
5. **Sector swing cap**: cap at 10–20% above entry_high depending on sector

**Assessment:**

Using 20d/50d highs as resistance is legitimate — these are real market structure levels where prior sellers exist. Stocks face natural resistance at recent highs because that is where people who bought the top are waiting to break even. This part of the logic is genuine swing trading thinking.

The sector-aware caps (bank 10%, mining 20%) correctly reflect IDX volatility profiles. A 15% swing in ADMF is realistic; a 15% swing in BBCA in a single swing trade is not.

**The critical flaw — Fair Value as target ceiling:**

```python
# debate_chamber.py ~line 3797
if fair_value and fair_value > 0 and target > fair_value:
    target = snap_to_tick(fair_value)
```

This single line is where the system stops being a swing trading system.

Price does not stop at fundamental fair value. Price stops at **resistance levels** — places where supply overwhelms demand. Those supply zones are: prior swing highs, round numbers, Volume Profile POC and HVN nodes, 52-week highs, VWAP compression zones. None of these have any reliable relationship to where a PE/DDM-based fair value estimate lands.

**What this does in practice:**
- Stock at Rp 3,000 with 20d resistance at Rp 3,800, FV = Rp 3,500 → target capped at Rp 3,500 (250 points below the real resistance). R/R is calculated off Rp 3,500 instead of Rp 3,800, making the setup look worse than it is.
- Stock at Rp 9,500 (BBCA) in momentum, FV = Rp 9,200 → target collapses to below current price → setup rejected. But BBCA breaking a 3-month range consolidation on 3× volume is a textbook swing long.

The code comment explains this ceiling was added to prevent INDO's R/R of 22.3x from a pre-crash resistance target. That is a legitimate problem — but the correct fix is the **5x implausibility ceiling already in the risk governor**, not a fundamental fair value anchor on the trade target. The FV ceiling solved the absurd-R/R problem at the cost of contaminating the entire target logic with value investing methodology.

**Second issue — inverted logic:**

Real swing trading target process:
1. Find the nearest significant supply zone (resistance)
2. Compute R/R: is the resistance far enough to justify the stop risk?
3. If R/R < minimum → pass on the trade

This system's process:
1. Seed at 2.0x R/R (mathematical, not market-structure-based)
2. Bump UP to resistance only if resistance is higher than the seed
3. Cap at FV

If the real next resistance is at 1.5x R/R (between current price and the 2.0x seed), the system ignores it and targets 2.0x R/R — right through the actual supply zone. The trade would likely reverse at the real resistance, and the system wouldn't know it.

**Verdict: Partially aligned, with one critical conceptual error**

The use of recent highs as resistance is correct swing trading logic. The FV ceiling is not — it substitutes fundamental equilibrium for technical supply identification, and that substitution corrupts the target for all stocks near or above their fundamental value.

---

## FAIR VALUE THRESHOLD — Conceptual Alignment

**What the code actually does** (fair_value_calculator.py):
- PE Band: `EPS × historical_avg_PE` (5-year API median where available)
- P/B Band: `BVPS × historical_avg_PB` with ROE < ke value trap cap
- DDM: `DPS / (ke − g)` via Gordon Growth Model, 15–25% weight
- EV/EBITDA for mining/energy only
- SOE governance discount: 15% applied to all BUMN stocks
- Historical valuation band: where current PE/PB sits in own multi-year range (HISTORICALLY_CHEAP / BELOW_AVG / ABOVE_AVG / HISTORICALLY_EXPENSIVE)

**The timeframe mismatch — the honest truth:**

Every method in this calculator answers the question "what is this company worth as a going concern?" That question operates on a 5–10 year investment horizon.

**DDM** is the most extreme example. The Gordon Growth Model — `DPS / (ke − g)` — is a perpetuity formula. It values the stock as the sum of all dividends paid to infinity, discounted back to today. A 7% growth assumption in the denominator means the model is sensitive to dividends paid 40–50 years from now. Using this to assess whether a 3–15 day swing trade is worth taking is a genuine timeframe mismatch. A company can be worth Rp 9,000 by DDM and trade at Rp 10,500 for the next six months during a bull run. DDM does not tell you anything useful about where the price will be in 10 trading days.

**PE Band** is less problematic — PE ratios do mean-revert in identifiable cycles and "expensive vs history" is genuinely useful context. But a 5-year historical average PE reflects equilibrium across full business cycles. A swing trade does not need equilibrium — it needs the next resistance level.

**Where the fair value module is actually doing useful swing work:**

The **Historical Valuation Band** (HISTORICALLY_CHEAP / BELOW_AVG / ABOVE_AVG / HISTORICALLY_EXPENSIVE) is the most swing-aligned element in the entire fair value module. Comparing current PE/PB to the stock's own history as a percentile rank is genuinely useful context. "BBCA's PB is at the 85th percentile of its 10-year range" tells you the stock is historically expensive and momentum is the only reason to be long. This is how real analysts use valuation in a swing context.

**The structural contradiction:**

The CIO prompt is philosophically correct: "flag overvaluation in reasoning, don't auto-AVOID, use R/R to decide." But the target ceiling implementation undermines this intent. You cannot say "fair value is just context" in the CIO prompt while simultaneously hard-capping the price target at fair value in the Python envelope. The architecture contradicts the philosophy.

The `risk_overvalued` flag propagates: FV calculator → CIO reads it → CIO can set `risk_overvalued = False` for momentum plays → Risk Governor reads from CIO verdict. This gives the CIO flexibility to override, which partially saves the system. But the target ceiling does not go through the CIO — it happens in Python before the CIO sees the setup. The CIO receives a pre-capped target and does not know it was capped by FV.

**Verdict: Most misaligned component**

DDM is conceptually wrong for swing trading context. The timeframe of fair value methods (multi-year equilibrium) is orthogonal to the timeframe of swing trading (3–15 days). The FV ceiling as a structural target cap applies value investing logic to a momentum pricing problem. The historical valuation band is the exception — this is swing-appropriate thinking.

---

## RISK GOVERNOR — Conceptual Alignment

**What the code actually does** (risk_governor.py):
- Hard rejects: `rating_not_buyable`, `low_confidence`, `overvalued`, `rr_too_low`, `rr_implausible`, `insufficient_technical_data`, `ara_entry_risk_high`
- Soft flags: `counter_trend_setup`, `fv_unmeasurable`, `historically_expensive`, `arb_lock_risk_high`
- Status routing: `deployable` / `conditional_deployable` / `wait_for_pullback` / `watchlist_only` / `reject`
- Regime override: DEFENSIVE → downgrade `deployable` to `watchlist_only`
- Circuit breaker: 3% realized daily portfolio loss → halt sizing

**Assessment:**

The **entry zone validation** is the most professionally sound element of the entire system. The four-status routing reflects exactly how professional traders think about execution:

- "Price is in the zone" → `deployable`
- "Good setup, price ran past the zone" → `wait_for_pullback`: don't chase, it will come back or it won't
- "Setup valid but price hasn't arrived yet" → `watchlist_only`: patience, monitor
- "Setup broken" → `reject`

This is not generic risk management copied from a textbook. This is genuine swing trading execution discipline.

**ARA enforcement** (`ara_entry_risk_high` → hard reject): Buying at ARA (Auto Rejection Alert — when a stock has already hit its daily upper limit and is queued for the next session) is one of the classic retail mistakes on IDX. Momentum chasers buy the ARA queue, only to watch the stock gap down when the next session opens. This code would not appear in a generic risk management template — it shows genuine IDX market mechanics knowledge.

**Regime-aware defensive downgrade**: Python computes market regime from JKSE realized volatility → regime propagates to trade envelope ATR multiplier (wider stops) → if DEFENSIVE, risk governor forces all setups to watchlist regardless of individual stock quality. This prevents being long individual names when the market index itself is in a downtrend.

**What is genuinely missing for IDX:**

**No liquidity gate.** IDX has hundreds of tickers where average daily turnover is Rp 100–500M. A Rp 50M position in such a stock creates visible price impact, and exiting in a downturn is genuinely difficult. The risk governor has no minimum volume or turnover threshold. It could generate a `deployable` verdict for a thinly-traded stock where slippage and exit risk would invalidate the R/R entirely.

**Counter-trend R/R not elevated.** Counter-trend setups (below MA200) appear as `conditional_deployable`, not rejected — and they still face only the 1.5x R/R minimum. This is incomplete risk management for a structurally lower win-rate setup type.

**Gap risk is addressed statistically but not positionally.** ATR-based stops provide statistical coverage, but the system does not reduce position size specifically when the next calendar event (FOMC, quarterly results, elections) creates binary overnight gap risk.

**Verdict: Most aligned component**

The entry zone routing, ARA enforcement, regime downgrade, and circuit breaker show genuine swing trading and IDX market knowledge. The liquidity gap and flat counter-trend R/R are real missing pieces, but the core framework is correctly conceived.

---

## System Philosophy Check

**System type: C — A hybrid that does not clearly know what it is**

**What pulls toward swing trading (short-term, technical, momentum):**
- Python-computed ATR stops, RSI, MACD, Bollinger, VWAP, Fibonacci, Volume Profile
- MA50 pullback entry zone logic
- 20d/50d high resistance identification
- Entry zone validation (`deployable` / `wait_for_pullback` / `watchlist_only`)
- ARA/ARB enforcement (pure IDX swing trading concern)
- Regime-aware ATR multiplier scaling
- Chartist prompt with 15 technical steps reading pre-computed indicators

**What pulls toward value investing (long-term, fundamental, equilibrium):**
- DDM at 15–25% weight in the fair value composite
- **Fair value as hard ceiling on trade targets** — the biggest philosophical contamination
- Conflict Resolution Matrix requiring both Fundamental AND Technical PASS for standard BUY
- SOE governance discount on fair value (multi-year structural discount applied to near-term swing)
- 5-year historical PE/PB multiples as the fair value anchor methodology

**The tension illustrated with a concrete IDX example:**

BBCA after a 3-month range consolidation breaks out above Rp 9,500 on volume 3× the 20-day average. RSI at 58 (not overbought). MACD positive expanding. Price above MA200. Above 20d VWAP. Bullish engulfing candle.

A real swing trader says: that is a BUY. Clean breakout, trend-aligned, volume confirms, momentum not stretched.

This system says: let me check if current price is above fundamental fair value. BBCA trades at a premium to long-term historical multiples in almost every bull market environment. The system would likely cap the target at the FV estimate (below actual resistance) or flag `risk_overvalued`, making the setup a HOLD at best.

The system is effectively saying "I will not swing-trade momentum runs in quality stocks unless they are also cheap on fundamentals." That is not swing trading philosophy — that is value investing criteria applied to a momentum signal.

---

## Would This System Actually Find Trades?

**In a bear market recovery (IDX 2020, 2022–2023):** Yes, well. Stocks are below historical fair values AND technically setting up. Both gates open simultaneously. This system would correctly identify good setups and avoid traps. This is the regime where the system performs closest to its design intent.

**In a neutral-to-mild bull market:** Partially. Stocks below FV get clean setups; stocks above FV get capped targets that underestimate actual resistance, creating artificially pessimistic R/R ratios.

**In a strong bull market (IDX 2021-type run):** Poorly. Most quality stocks trade above their fundamental fair values during a sustained bull run. The system would generate mostly HOLD verdicts precisely when the best momentum trades are appearing. This is the most dangerous failure mode — the system becomes most conservative exactly when market opportunity is highest.

**In practical signal count terms:** The combination of requiring Fundamental PASS + Technical PASS + price in entry zone + R/R above minimum + not overvalued produces a set of conditions that rarely all align in a trending market. Signal output would be skewed toward mean-reversion setups (cheap stocks bouncing from oversold) rather than momentum/breakout setups (strong stocks getting stronger).

---

## Final Verdict

```
STRATEGIC ALIGNMENT VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Is this genuinely swing trading logic?       : PARTIALLY

It is a quality-filtered momentum system where
fundamental fair value was allowed to colonize
the target price — the one place it has no
business being.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Component most misaligned : FAIR VALUE

  DDM (Gordon Growth Model) is a perpetuity
  model with a decades-long horizon — it has
  no valid input into a 3–15 day trade decision.
  More critically, FV as a target ceiling conflates
  "what is this company worth long-term" with
  "where will sellers emerge this week." These
  are unrelated questions answered by unrelated
  methods.

Component most aligned    : RISK GOVERNOR

  The entry zone routing, ARA enforcement, and
  regime-aware defensive downgrade show genuine
  IDX swing trading knowledge. This is not
  boilerplate risk management — it reflects how
  an experienced IDX trader thinks about execution
  timing and position management.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

HONEST ASSESSMENT:

You are on a partially right path. The technical
infrastructure is genuinely good — Python-computed
ATR stops, 20d/50d resistance anchoring, regime
scaling, ARA gate, entry zone validation. These
reflect real swing trading discipline. The
philosophical error is specific: fundamental fair
value was allowed to set a ceiling on where the
trade can target. A price target in swing trading
must come from market structure (supply zones,
resistance levels), not from a PE × historical_PE
calculation. The current architecture produces a
system that works best in cheap markets recovering
from oversold conditions — which is a value
investing outcome, not a swing trading one.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## What Needs to Change Conceptually

These are philosophy changes, not code fixes.

### 1. Remove FV as target ceiling
Keep FV as context for the CIO's conviction narrative. Remove the FV ceiling from `_compute_trade_envelope` entirely (debate_chamber.py lines ~3797–3799). The 5x implausibility ceiling in the risk governor already handles absurd R/R without needing a fundamental anchor on the target.

### 2. Replace DDM with a timeframe-appropriate mean-reversion signal
DDM belongs in a long-only dividend-growth portfolio manager's toolkit, not here. Replace it with something like: "Is the stock more than 2σ below its 52-week VWAP?" or "Is the stock at the bottom quartile of its own 52-week range?" These are mean-reversion signals with the right temporal horizon for swing trading.

### 3. Raise R/R minimum for counter-trend setups
Price below MA200 should require 2.5x R/R minimum, not 1.5x. The lower win rate of counter-trend trades requires higher R/R to maintain positive expected value. This is a math constraint, not a preference.

### 4. Make the Conflict Resolution Matrix momentum-aware
Currently: Fundamental FAIL + Technical PASS = momentum play at 50% size, plus additional gates (foreign flow AND volume confirmation required). In a momentum environment, Technical PASS alone should be sufficient if the stock clears a minimum quality bar (not loss-making, not in negative equity). The current two-gate requirement structurally disadvantages momentum setups that are fundamentally fair-but-not-cheap.

### 5. Add a liquidity gate
Any stock with less than Rp 1B average daily turnover should require explicit liquidity acknowledgment before deployment. IDX liquidity is more heterogeneous than most markets; slippage risk on small-caps can invalidate the R/R entirely.

---

*This audit evaluates conceptual alignment with swing trading methodology, not code correctness. File references point to the codebase state as of 2026-06-21.*

---

---

# Strategic Alignment Audit — Part II: Full System (Excluding R/R, Target, FV, Governor)

**Date:** 2026-06-22
**Scope:** Every component NOT covered in Part I above: prompt corpus, regime detection, quant filter, evidence ranker, position sizer, portfolio optimizer, historical scorer.
**Question:** Outside the four areas already fixed or assessed in Part I, does the rest of the system behave like a competent 5-20 trading-day swing trader?

---

## Finding 0 — Holding Period Contract: What Is "Swing Trading" Here?

This needs to be stated once and anchored, because every other finding depends on it.

**What the codebase says:**
- `schemas/debate.py` docstring: "5-20 trading-day swing execution frame"
- `CIOVerdict.timeframe` default: `"5-20 Trading Days"`
- `CIOVerdict.execution_horizon_days` default: `10`
- `fundamental_scout.txt` keeps medium-term catalysts as context only
- Every debate prompt, bull/bear role, CIO judge: "5-20 trading-day execution horizon"
- `CLAUDE.md`: describes the system as "swing trading"

**What "swing trading" means in standard practice:**
- Classic definition: 2–10 trading days (some extend to 3 weeks)
- "Position trading": weeks to 3 months
- The previous medium-term contract was closer to *position trading* than classic swing trading

**Verdict:** The system is now anchored to a 5-20 trading-day execution contract. Medium-term catalysts remain context, but BUY requires near-term technical or flow confirmation.

This framing matters: MA200 and weekly trend filters are structural context for the 5-20 trading-day setup, not standalone reasons to buy. A medium-term catalyst is useful context only when price action, volume, or flow confirms inside the execution window.

---

## Prompt Corpus — Alignment

### Fundamental Scout (`fundamental_scout.txt`)

**Role as designed:** Provide fundamental context (valuation, quality checks, growth catalyst) for the debate. Output: BULLISH / NEUTRAL / BEARISH.

**What BULLISH means in this prompt (lines 78–81):**
```
BULLISH — current price is UNDERVALUED vs fair value; margin of safety exists.
BEARISH — current price is OVERVALUED vs fair value; no margin of safety.
NEUTRAL — price is FAIRLY VALUED, or fair value data unavailable.
```

**The problem:** This is a *value investing* output signal, not a *swing timing* signal. A stock can trade at 20% above fair value for 12 consecutive months during a bull cycle and still be the best 5-20 trading-day continuation setup available. The scout declaring it BEARISH (overvalued) early in that cycle would systematically bias the entire debate toward HOLD/AVOID for precisely the stock a momentum swing trader most wants to own.

The FAIL/PASS matrix in `cio_judge.txt` (Task E) partially corrects this: when Fundamental FAIL + Technical PASS + volume breakout, a momentum BUY is still possible. But the scout's BEARISH output travels through all debate rounds and confidence calculations, creating a structural drag on confidence scores for all above-fair-value setups even when the CIO correctly overrides.

**What would be more aligned:** The scout should output its valuation finding and quality flags without labeling the stock BULLISH/BEARISH on that basis alone. The scout's output is useful context; it should not be a directional verdict. The direction verdict belongs to the chartist and CIO synthesis.

**STEP 4 (Growth Catalyst):** Medium-term catalyst context is useful, but it is not sufficient for BUY without a 5-20 trading-day technical or flow trigger.

**STEP 6 (Post-Earnings Drift, Insider Selling):** Both are genuine swing signals. Insider selling reducing confidence by a forced cap is appropriate; post-earnings drift window is a real short-to-medium term effect. Well aligned.

**Verdict: Partially aligned.** The BULLISH/BEARISH definitions are value investing framing applied to a swing timing slot. The content (quality checks, catalysts, insider flags) is otherwise swing-appropriate.

---

### Chartist (`chartist.txt`)

**Overall:** 15 steps covering MA200, EMA20, ATR stop, RSI, volume, weekly trend, MACD, candlestick patterns, Bollinger Bands, RSI divergence, gap, compression, VWAP, AVWAP, Fibonacci. This is a genuinely comprehensive technical analysis framework, not a boilerplate prompt. Almost all of it is directly swing-trade relevant.

**One active discrepancy — stop-loss level:**

The chartist says (STEP 4):
> "STOP-LOSS: Rp W,WWW (1.5 x ATR below EMA20)"

The Python envelope (`REGIME_ATR_STOP_MULTIPLIER` in `utils/technicals.py` + `_compute_trade_envelope` in `debate_chamber.py`) uses:
```python
stop = max(
    sma20 - 1.0 × ATR,          # SMA20-anchored floor
    current_px - 2.5 × ATR,     # Price-anchored floor (2.5× in NORMAL regime)
)
# Hard floor: ≥ 88% of current price
```

The chartist tells the LLM to debate around a 1.5× stop. The executed stop is 2.5× (NORMAL) or 3.0× (DEFENSIVE). This means:

- Bear R2's stress test (`bear_r2.txt` line 27: "maximum 1-week adverse move = 2 × ATR") checks whether `2×ATR ≥ Bull's claimed margin of safety` using the debate reference stop, not the real stop. If the real stop is already at 2.5×ATR, the bear's 2×ATR stress test never fires — and the bear doesn't know why, because the chartist told the LLM 1.5×.
- When Bull argues that MA50 or EMA20 "held as support," they are implicitly defending a stop slightly above those levels — which doesn't match the wider Python stop.

Every trade envelope arrives pre-computed from Python and the CIO is instructed to use it verbatim (cio_judge STEP 6). The debate agents' stop references are irrelevant to the final trade output. The practical damage is: debate-round arguments about stop validity are semantically disconnected from the actual executed stop, reducing debate quality without affecting the final output.

**Verdict: Structurally well aligned, with one stop-level mismatch that degrades debate quality but does not corrupt the final output.**

---

### Sentiment Scout (`sentiment.txt`)

**Contrarian intensity signal:** EXTREME_BULLISH (≥80% bullish discussion) → contrarian sell signal. EXTREME_BEARISH → contrarian buy signal. This is correct swing trading psychology: crowd extremes mark reversal points, not continuation. Well aligned.

**CIO threshold:** `sentiment.confidence >= 0.7` for the +0.02 bonus. A threshold of 0.70 is high — in practice Stockbit social data is noisy and low-confidence, so most sentiment readings will miss this bonus. The effect is a slight underweight on sentiment in the CIO calibration, which is arguably appropriate given data quality on IDX.

**Verdict: Aligned.** Sentiment as a contrarian signal at extremes is appropriate for 5-20 trading-day swing frames.

---

### Bull R1 / Bear R1 / Bull R2 / Bear R2

All four now anchor the swing execution frame at 5-20 trading days and require:
- One fundamental metric with actual number
- One technical metric with actual number
- One company-specific catalyst (not generic macro)
- Specific R/R statement

The data-citation requirement is the strongest alignment feature: it forces LLMs to work from specific numbers, not from generic bullish/bearish narratives. The "DO NOT repeat any price or argument from Round 1" cross-examination rule prevents circular arguments.

**Bear R2 stress test:**
> "Maximum 1-week adverse move = 2 × ATR(14). IF 2 × ATR(14) >= Bull's claimed margin of safety → declare: 'Trade is unviable for swing execution'"

For a 5-20 trading-day execution horizon, a 1-week stress test is appropriate as an *entry quality* check (can you survive the first week without being stopped out). It is not a full holding-period stress test, nor is it meant to be. Appropriate.

**Verdict: Well aligned.** Cross-examination structure and specific-citation requirements are genuine improvements over open-ended debate.

---

### Consensus (`consensus.txt`)

Four disagreement types: `direction`, `timing`, `valuation`, `catalyst`. Note on `timing`: "most common in a sideways IHSG market — use it when both agents agree the stock is good but disagree on entry point or RSI readiness." This is IDX-specific and appropriate for a 5-20 trading-day execution horizon.

**Verdict: Aligned.**

---

### Devil's Advocate (`devils_advocate.txt`)

**Challenge 2 (Transaction Cost Stress Test):**
Round-trip costs: 0.15% buy + 0.25% sell + 0.10% income tax + 0.15–0.30% slippage = ~0.65–0.80% total.
Net return threshold: < 2.0% = INSUFFICIENT, 2.0–3.0% = MARGINAL, > 3.0% = VIABLE.

For 5-20 trading-day swing trades targeting 3–10% gains, a 2% net return minimum is appropriate — it ensures transaction costs aren't consuming the majority of the swing gain. For a 3% target swing, 0.8% costs leaves 2.2% net, which clears the threshold. For a 4% target, 3.2% net = VIABLE. Correctly calibrated.

**Verdict: Well aligned.** The transaction cost challenge is the most IDX-specific and swing-appropriate stress test in the entire system.

---

### CIO Judge (`cio_judge.txt`)

**Phase B Calibration — 30-day catalyst bias:**

```
[+0.02] Specific catalyst confirmed within 30 days
```

This finding is superseded by the 5-20 trading-day execution contract. The +0.02 bonus now belongs to a near-term technical or flow trigger, while later catalysts remain medium-term context.

The practical magnitude is small (+0.02 confidence), but the direction changes under the new contract: catalyst timing alone should not receive execution credit unless near-term price action confirms.

**CONFLICT RESOLUTION MATRIX:** The Task E fix (FAIL/PASS → momentum BUY with volume confirmation) is the right correction. The PASS/FAIL → HOLD is also correct: fundamentally good stocks that haven't confirmed technically should wait for confirmation. The matrix is now properly tiered.

**DEFENSIVE regime circuit breaker logic (cio_judge lines 28–30):**
> "IF regime = DEFENSIVE AND R/R < 2.0: AVOID is strongly preferred."

This is a meaningful regime-specific constraint. Under DEFENSIVE conditions, the CIO further raises the effective R/R floor through its own AVOID preference. This is additive to the risk governor's regime downgrades — appropriate and swing-aligned.

**Verdict: Mostly aligned**, with the catalyst-bonus issue superseded by the 5-20 trading-day trigger rule.

---

## Regime Detection (`core/regime.py`)

**Five regimes:** DEFENSIVE, RECOVERY, HIGH, NORMAL, LOW. Detection logic:
- DEFENSIVE: 5d weekly drop ≤ −5% OR JKSE close < MA20 AND MA50 AND MA200
- RECOVERY: HIGH vol + 5d weekly return ≥ +10%
- HIGH/NORMAL/LOW: 20-day realized volatility bands (2% / 1–2% / <1%)

**Regime propagation across the system:**

| Component | Uses Regime? |
|---|---|
| Quant filter ATR stop multiplier | ✅ |
| Debate chamber (injected to prompts) | ✅ |
| CIO judge (circuit breaker, defensive AVOID preference) | ✅ |
| CIO calibration (-0.01 for LQ45 in DEFENSIVE) | ✅ |
| Risk governor (regime downgrade to watchlist, Task D R/R floor) | ✅ |
| Position sizer (trailing stop regime-scaling) | ✅ |

The system has consistent regime propagation from detection through to every decision layer. This is one of the most complete implementations in the codebase.

**One known gap — ATR multiplier doesn't differentiate HIGH from NORMAL:**
```python
REGIME_ATR_STOP_MULTIPLIER = {
    "LOW": 2.5, "NORMAL": 2.5, "HIGH": 2.5, "RECOVERY": 2.5, "DEFENSIVE": 3.0
}
```
In HIGH volatility regime, intraday ranges expand — the same 2.5× multiplier that works in NORMAL produces a stop that's too tight relative to the realized noise band. The code comment in `technicals.py` explicitly flags this as a deferred calibration decision. It is a real gap but a known one.

**Verdict: Well aligned and comprehensively integrated.** The flat HIGH/NORMAL/RECOVERY ATR multiplier is a documented calibration deferral, not an oversight.

---

## Quant Filter — Screening Criteria and Scoring (`core/quant_filter/`)

### Score Weights (config.py)
```
70% Technical Momentum: RSI 25 + Volume 25 + Price Momentum 20
30% Fundamentals:       Valuation 20 + Profitability 10
```

A 70/30 technical/fundamental split is appropriate for swing trading. Most professional momentum screeners are 100% technical; the 30% fundamental weight provides a quality floor without dominating the momentum signal.

### Screening Gates — Alignment Assessment

| Gate | Value | Assessment |
|---|---|---|
| Price > EMA20 (momentum mode) | required | ✅ Aligned — entry in uptrend |
| RS vs IHSG ≥ 0 (1 month) | required | ✅ Aligned — sector relative strength |
| RSI hard reject | > 70 | ✅ Aligned — don't buy overbought |
| ADT 20d | ≥ Rp 10B | ✅ Aligned — liquidity floor |
| ATR% | ≤ 5% | ✅ Aligned — cap on excessive volatility |
| ExDate CRITICAL | exclude | ✅ Aligned — ex-div risk gate |
| Volume surge hard gate | ≥ 0.30× | ⚠️ Too permissive |

**Volume gate issue:** `min_volume_surge_for_candidate = 0.30` means a stock trading at 30% of its 20-day average volume still passes the filter. A stock at 0.30× average volume is showing sharply reduced participation — exactly the opposite of what a swing trader needs. Most swing entry criteria require at least 0.80× average volume at screening time, and 1.5–2× for breakout confirmation. The current threshold effectively means "not completely dead," not "showing momentum."

This gate is distinct from the volume confirmation step inside `_analyze_ticker` (where `vol_surge_ratio` is scored and affects composite score). A stock at 0.35× average volume passes the gate and only takes a 10% weight on the volume scoring sub-component — producing a candidate with acceptable composite score but genuinely poor volume confirmation.

### RSI Scoring Asymmetry

```
Oversold (<45):       40% weight (potential reversal)
Accumulation (45-55): 100% weight (sweet spot)
Uptrend (55-70):      80% weight
Overbought (>70):     hard reject
```

This asymmetry is explicitly documented as "swing-trade aware" and is correct. RSI in the accumulation zone (45–55) is the ideal entry for momentum continuation: momentum is positive but not stretched. The 80% weight for uptrend RSI (55–70) still rewards strength without penalizing it.

### Static Filter: PBV < 80th sector percentile

This filter has a notable side effect: it systematically excludes momentum leaders. In a strong sector rally, the stocks with the most price appreciation will be near the 80th percentile PBV or above. The filter removes the strongest stocks precisely because they ran the hardest. For a 5-20 trading-day system targeting continuation setups, this creates tension: the stocks most clearly in an uptrend may be excluded.

The filter was designed to avoid overvalued stocks at peak PBV. But it conflates "high PBV rank" with "overvalued" — a stock can be at the 85th percentile PBV in its sector because it's genuinely a quality business growing faster than peers.

**Verdict: Mostly aligned, with two items to watch:**
1. Volume gate threshold (0.30×) is too permissive for momentum screening
2. PBV 80th percentile sector filter may exclude momentum leaders in uptrending markets

---

## Evidence Ranker — Category Weights (`services/evidence_ranker.py`)

```python
CATEGORY_WEIGHTS = {
    "fair_value": 1.0,
    "fundamental": 0.9,
    "technical": 0.85,
    "sentiment": 0.6,
    "exdate": 0.7,
    "metadata": 0.3,
}
```

The ranking puts `fair_value` and `fundamental` above `technical`. For swing trading, the correct priority is inverted: technical (entry timing, momentum, setup quality) is the primary signal; fundamental (quality filter, context) is secondary.

The practical effect: when the evidence bundle is assembled for the LLM prompts, the most space-efficient (highest score) chunks are fundamental and fair-value data. If the bundle hits `MAX_BUNDLE_CHARS = 2,400`, technical chunks are the ones most likely to get cut.

Mitigating factor: fair_value is always force-pinned first in `select_evidence()`, guaranteeing its inclusion. Technical data is still likely to appear since it's the third category. The practical degradation is modest.

**Staleness handling** (`STALE_THRESHOLD_SECONDS = 86,400` — 24 market hours) correctly excludes weekends and IDX holidays via `_market_freshness_seconds`. This is excellent IDX-specific engineering.

**Verdict: Minor misalignment in category weights.** For swing trading, technical data should score ≥ fundamental (suggested: technical=0.95, fundamental=0.85, fair_value=0.80). Practical impact is limited because technical chunks are usually selected regardless.

---

## Position Sizer (`core/quant_filter/position_sizer.py`)

**Capital deployment:** Target 40–70% of capital, capped at 95%. Appropriate for a swing portfolio — enough deployment to generate returns, enough cash reserve to act on new signals.

**Entry price basis:** `entry_high` (worst-case fill within the entry zone) for all risk calculations. This is the professional standard and prevents underestimating position risk.

**Lot sizing logic:**
```python
lot_from_risk  = floor(max_loss_budget / (risk_per_share × LOT_SIZE))
lot_from_alloc = floor(capital_allocated / (entry_price × LOT_SIZE))
final_lot      = min(lot_from_risk, lot_from_alloc)
```

Conservative minimum of risk-based and allocation-based lot sizes. Correct.

**Trailing stop integration:** ATR-based trailing stop parameters attached to each position when `atr14` data is available. This is the correct exit mechanic for swing trades that go in your favor — let winners run with a moving stop rather than selling at a fixed target.

**Partial exit plan (T1 50%, trail remainder):** Standard two-tranche exit for BUY and STRONG_BUY. Counter-trend below MA200 forces 75% exit at T1. Weekly downtrend forces 100% exit at T1. These regime-aware modifications are textbook professional practice.

**Verdict: Well aligned.** Position sizing is one of the most swing-trade-competent components in the entire system.

---

## Portfolio Optimizer (`core/portfolio_optimizer.py`)

**Greedy sector cap** with soft-cap fallback and tie-breaking: appropriate diversification logic to avoid sector concentration.

**`min_conviction` by regime:**
```
DEFENSIVE: 0.70 (very selective in bear markets)
HIGH:      0.45
RECOVERY:  0.40
NORMAL:    no override
LOW:       0.20 (permissive in calm markets)
```

The LOW regime threshold (0.20) is permissive but self-limiting: conviction scores below 0.40 produce HOLD verdicts with 10% base allocation and 0.45 weight, so low-conviction candidates in LOW regime receive very small position sizes. The 0.20 threshold is more permissive than ideal but not operationally harmful given the sizing mechanics downstream.

**Verdict: Aligned.**

---

## Historical Scorer (`core/historical_scorer.py`)

**Two-tier win rate system:**
1. Debate-history win rate: BUY/STRONG_BUY with confidence > 0.50 in past runs
2. Realized outcome win rate: actual trade P&L from `BacktestMemory`

The realized outcome tier is the correct final arbiter. The two-tier approach (fall back to debate history if realized outcomes < 10 records) is pragmatic given limited backtest data early in the system's life.

**`_MIN_RECORDS_FOR_ADJUSTMENT = 10`:** Most tickers will have < 10 records until the system has been running for some time. In practice, the historical scorer is largely inactive for new tickers — which is correct behavior (avoid adjusting scores based on insufficient statistical evidence).

**`_EV_HIGH_THRESHOLD = 3.0%`** (avg P&L ≥ 3% = bonus): Maps correctly to the 3–10% swing trade target. Appropriate.

**No time-decay on historical records:** Past records from different market regimes are weighted equally. A BUY signal from 18 months ago in a different regime affects conviction scoring equally to one from 2 months ago. This is a known limitation but not a fundamental misalignment.

**Verdict: Conceptually aligned.** The realized P&L tier is correctly designed.

---

## System-Level Summary

### What Is Well Aligned (Part II)

1. **Regime pipeline:** Detection → propagation → ATR stops → conviction gates → position sizing → partial exit. Fully consistent end-to-end.
2. **Price computation isolation:** Python computes all OHLCV-derived numbers. LLMs only interpret. Prevents hallucinated prices in trade decisions.
3. **IDX-specific mechanics:** ARA/ARB detection, tick-size snapping, T+2 settlement note, IDX circuit breaker awareness, Indonesian trading session windows, IDX holiday calendar in staleness calculations.
4. **Exit discipline:** Two-tranche exits, ATR trailing stops, regime-sensitive T1 pct, anti-averaging-down rules — genuine swing portfolio management.
5. **Data quality gates:** XLSX staleness blocking pipeline after 5 days, degrading after 3 days.

### What Is Not Well Aligned (Part II)

1. **Fundamental Scout BULLISH/BEARISH = value signal, not swing timing signal.** Contaminates debate rounds for above-FV momentum candidates throughout all rounds. Partially mitigated by FAIL/PASS matrix (Task E) but structurally embedded.

2. **Chartist stop reference (1.5× ATR) doesn't match Python envelope (2.5× ATR).** LLM debate arguments about stop validity are semantically disconnected from the actual executed stop. Final output is unaffected; debate quality suffers.

3. **CIO catalyst bonus now belongs to a 5-20 trading-day technical/flow trigger.** Medium-term catalysts are context only.

4. **Volume gate threshold (0.30×) too permissive.** Candidates with 30–60% of average volume reach the debate engine, diluting momentum quality.

5. **Evidence ranker weights: fundamental > technical.** Directionally wrong for swing trading context. Practical impact modest.

---

## Part II Summary Verdict

```
PART II STRATEGIC ALIGNMENT VERDICT
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Overall alignment (excluding the 4 Part I areas):   MOSTLY ALIGNED

The 5-20 trading-day execution frame is consistently applied across
prompts, schemas, calibration rules, and exit
mechanics. The regime pipeline is the best-engineered
component: end-to-end from detection through position
sizing with zero gaps.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Most aligned: Position Sizer + Regime Pipeline
  - ATR-trailing stop, lot sizing, partial exit, and
    regime-sensitive T1 allocation are all correctly
    conceived swing trade mechanics.
  - Regime propagation is complete and consistent.

Partially misaligned: Fundamental Scout
  - BULLISH/BEARISH output uses value investing framing
    (price vs fair value), not swing timing framing.
  - The scout should surface fundamental context as
    evidence, not as a directional verdict. Direction
    belongs to the chartist and CIO synthesis.

Minor gaps:
  - Chartist stop mismatch (debate quality only;
    final output unaffected)
  - CIO 30-day catalyst bonus bias (±0.02 confidence)
  - Volume gate too permissive (0.30× vs recommended
    0.80× for momentum screening)
  - Evidence ranker category weights favor fundamental
    over technical

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

COMBINED VERDICT (Part I + Part II):

The system has a coherent 5-20 trading-day swing execution framework
everywhere except where fundamental fair value was
allowed to determine trade targets (Part I) and where
the fundamental scout was given a directional output
slot that contaminates momentum analysis (Part II).

The infrastructure is correctly built. The philosophy
is correctly stated. Two specific design decisions —
FV ceiling on targets, FV framing on scout output —
import value investing logic into swing timing slots
where it doesn't belong.

Fixing these two decisions would produce a genuinely
consistent 5-20 trading-day swing trading system.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Prioritized Fix List (Part II Only)

**P1 — Volume gate threshold (quick fix, high impact):**
Raise `min_volume_surge_for_candidate` from `0.30` to `0.80` in `core/quant_filter/config.py`. A stock at 80% average volume is showing normal activity; a stock at 30% is showing disengagement.

**P2 — Fundamental Scout directional output (medium effort, structural):**
Remove BULLISH/BEARISH from `fundamental_scout.txt` output format. Replace with a neutral output:
```
Valuation Context: UNDERVALUED / FAIRLY_VALUED / OVERVALUED
Quality Flag: PASS / CONDITIONAL / FAIL
Catalyst Context: [medium-term event or NONE]
Agent Confidence: 0.xx
```
This removes the value investing directional vote from the fundamental slot while preserving all the useful information (quality, valuation context, catalyst). The CIO uses these fields directly anyway.

**P3 — Chartist stop reference:**
Update `chartist.txt` STEP 4 from "1.5 × ATR" to "2.5 × ATR (or 3.0 × ATR in DEFENSIVE regime)" to match `REGIME_ATR_STOP_MULTIPLIER`.

**P4 — CIO catalyst bonus window:**
Change Phase B from catalyst timing to a specific 5-20 trading-day technical/flow trigger in `cio_judge.txt`.

**P5 — Evidence ranker weights:**
Adjust `CATEGORY_WEIGHTS` in `services/evidence_ranker.py`:
```python
"technical": 0.95,      # was 0.85 — primary timing signal
"fair_value": 0.85,     # was 1.00
"fundamental": 0.80,    # was 0.90
```

---

*Part II audit covers codebase state as of 2026-06-22. Excludes R/R Threshold, Target Price, Fair Value Threshold, and Risk Governor — those are assessed in Part I above.*
