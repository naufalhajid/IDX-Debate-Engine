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
