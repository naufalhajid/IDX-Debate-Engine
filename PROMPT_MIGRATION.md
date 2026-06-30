# Prompt Migration Log

## 2026-06-30 — `cio-regime-labels-v27`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (DEFENSIVE → BEAR_STRESS in 3 places)
- `services/debate_prompts/manifest.json` (version → `2026-06-30-cio-regime-labels-v27`)
- `tests/test_debate_chamber_reliability.py` (version assertion updated)

### Changes

**`cio_judge.txt`** — Replaced stale legacy regime labels with HMM labels:

1. Circuit breaker warning: `IF regime = DEFENSIVE` → `IF regime = BEAR_STRESS`
2. R/R AVOID bias: `IF regime = DEFENSIVE AND R/R < 2.0` → `IF regime = BEAR_STRESS AND R/R < 2.0`
3. PHASE B LQ45 penalty: `regime = DEFENSIVE` → `regime = BEAR_STRESS`

**Why:** After HMM integration, `_regime_label_from_state()` returns the HMM label
(`BULL`/`SIDEWAYS`/`BEAR_STRESS`) preferentially. The CIO prompt used legacy labels
(`DEFENSIVE`/`RECOVERY`), so those three conditions never triggered — silently disabling
circuit-breaker warnings, the low-R/R AVOID bias, and the LQ45 penalty in stress regimes.

**Behavioral change:** In BEAR_STRESS regime the CIO will now correctly add circuit-breaker
risk to `weighted_reasoning`, prefer AVOID when R/R < 2.0, and apply the -0.01 LQ45
confidence penalty. No change in BULL or SIDEWAYS regime.

---

## 2026-06-24 — `id-sentiment-bilingual-v26`

**Files changed:**
- `services/debate_prompts/sentiment.txt` (BAHASA INDONESIA HANDLING block added)
- `services/debate_prompts/manifest.json` (version → `2026-06-24-id-sentiment-bilingual-v26`)
- `tests/test_debate_chamber_reliability.py` (version assertion updated)
- `services/indonesian_nlp.py` (new: `preprocess_indonesian_text`, `detect_language`)
- `services/news_fetcher.py` (`_extract_text` uses `preprocess_indonesian_text`; `bundle_to_prompt_string` annotates language)

### Changes

**`sentiment.txt`** — Added `BAHASA INDONESIA HANDLING` section before CONSTRAINTS.

New guidance covers:
- **Negation**: "tidak naik" / "bukan naik" → BEARISH (negation reverses direction)
- **Slang**: "cuan" = profit (BULLISH), "nyangkut" = stuck at loss (BEARISH), "mantap"/"jos" = approval (BULLISH), "gorengan" = pump stock (red_flag)
- **Pump coordination**: "ayo beli" / "gas" / "angkat" repeated without data → flag in red_flags
- **IDX events**: ARA/limit-up → BULLISH; ARB/limit-down → BEARISH; IHSG direction = context only; suspensi/delisting/fraud → BEARISH
- **Mixed language**: English terms (uptrend, breakout, cut loss) alongside Indonesian → interpret normally

**`services/indonesian_nlp.py`** (NEW) — Indonesian NLP utilities wired into the production flow:
- `preprocess_indonesian_text(text)`: Unicode NFKC normalization, URL removal, whitespace normalization. Applied to all text assembled by `_extract_text()` before keyword matching.
- `detect_language(text)`: Ratio-based heuristic using Indonesian marker words. Returns 'id', 'en', or 'mixed'. Applied in `bundle_to_prompt_string()` to annotate language of news headlines.
- `INDONESIAN_FINANCIAL_STOPWORDS`: Documented only — NOT stripped before keyword matching (would break INSIDER_SELLING detection for "jual saham", "menjual saham", etc.).

**Why:** Stockbit social posts are predominantly Bahasa Indonesia, but `sentiment.txt` was English-only with no Indonesian-specific guidance. Negation ("tidak naik" ≠ "naik"), IDX slang ("cuan", "nyangkut", "gorengan"), and pump coordination signals ("ayo beli") were not recognized. Gemini already handles Indonesian natively; these instructions activate its existing capability for IDX-specific financial language.

**Behavioral change:** Sentiment LLM will now correctly classify negated Indonesian phrases, recognize IDX-specific slang, and flag pump coordination patterns. News brief gains a `Language:` annotation from `detect_language`.

---

## 2026-06-24 — `fibonacci-low-confidence-v25`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 15 header + caution block updated)
- `services/debate_prompts/manifest.json` (version → `2026-06-24-fibonacci-low-confidence-v25`)
- `tests/test_debate_chamber_reliability.py` (version assertion updated)

### Changes

**`chartist.txt`** — STEP 15 (Fibonacci Retracement) labelled as low-confidence context only.

Before: `STEP 15 — FIBONACCI RETRACEMENT:` with authoritative interpretation language.

After: `STEP 15 — FIBONACCI RETRACEMENT [LOW CONFIDENCE — CONTEXT ONLY]:` with explicit caution:
- `CAUTION: Fibonacci retracement has no peer-reviewed IDX-specific validation.`
- `Use as supplementary context only — NOT as a primary entry/exit signal.`
- `Weight below: MA200 (STEP 2), structural swing-low support (STEP 3), and volume (STEP 4).`
- All Fibonacci commentary prefixed with `[Unvalidated on IDX]`.

**Why:** Gap analysis audit (GAP-09, 2026-06-23) found no peer-reviewed IDX study validating Fibonacci retracement as a predictive signal on BEI securities. Structural support/resistance from swing lows and ATR-based stops are better empirically validated. Leaving Fibonacci without caveat risked the chartist LLM treating it as a primary signal, introducing unvalidated noise into the debate.

**Behavioral change:** Chartist output will prefix Fibonacci commentary with `[Unvalidated on IDX]`, signaling to downstream agents (CIO judge, bull/bear) that this evidence is lower-weight than MA200/volume/momentum signals.

---

## 2026-06-24 — `ocf-rnoa-factor-v24`

**Files changed:**
- `services/debate_prompts/fundamental_scout.txt` (STEP 3 + STEP 5 extended)
- `services/debate_prompts/bull_r1.txt` (DATA AUDIT + FUNDAMENTAL FLOOR updated)
- `services/debate_prompts/bear_r1.txt` (DATA AUDIT + OVERVALUATION CHECK updated)
- `services/debate_prompts/manifest.json` (version → `2026-06-24-ocf-rnoa-factor-v24`)
- `tests/test_debate_chamber_reliability.py` (version assertion updated)

### Changes

**`fundamental_scout.txt`** — RNOA and OCF/Price wired into STEP 3 (Support Metrics) and STEP 5 (Quality Check).

STEP 3 additions:
- `RNOA or ROA fallback; prefer RNOA when present`
- `OCF/Price; prefer this value signal over P/B for non-financial stocks`

STEP 5 new quality gates:
- `IF OCF/Price < 3% → Flag: "Weak Cash Yield -- valuation is not strongly cash-backed."`
- `IF RNOA < 12% → Flag: "Weak Operating Profitability -- RNOA below quality threshold."`

**`bull_r1.txt`** — DATA AUDIT prefers OCF/Price and RNOA as primary fundamental citation. FUNDAMENTAL FLOOR instructs bull agent: `Prefer OCF/Price and RNOA/ROA evidence over momentum-only arguments`.

**`bear_r1.txt`** — DATA AUDIT includes OCF/Price and RNOA as risk metrics. OVERVALUATION CHECK adds: `Do not over-credit price momentum if OCF/Price or RNOA/ROA quality is weak.`

**Why:** `core/fundamental_factors.py` (IDX4 factor model) introduced RNOA and OCF/Price as higher-fidelity signals than ROE and P/B, but the debate agents still cited legacy metrics. This wires the new signals into LLM context.

---

## 2026-06-22 — `s1a-fundamental-scout-abstain-v23`

**Files changed:**
- `services/debate_prompts/fundamental_scout.txt` (OUTPUT FORMAT redesigned)
- `services/debate_prompts/manifest.json` (version → `2026-06-22-s1a-fundamental-scout-abstain-v23`)

### Changes

**`fundamental_scout.txt`** — Scout output redesigned to provide valuation evidence, not a directional vote.

Previously the scout emitted `Position: BULLISH|NEUTRAL|BEARISH`, mapping directly to BUY/HOLD/AVOID
in vote consensus. This contaminated swing-trade debate: a stock trading at a discount always voted
BUY regardless of momentum, trend, or timing — a value-investing framing, not a swing framing.

New output format:
- `Position: HOLD` (always) — scout abstains from directional timing vote
- `Valuation Context: UNDERVALUED | FAIRLY_VALUED | OVERVALUED` — replaces BULLISH/NEUTRAL/BEARISH
- `Quality Flag: PASS | CONDITIONAL | FAIL` — explicit financial health signal for CIO context
- `Catalyst: [event within 1-3 months] | NONE` — structured catalyst field

**Why no parsing changes in `debate_chamber.py`:** `Position: HOLD` routes correctly through the
existing `_POSITION_RE` → `_normalise_position` path. The CIO conflict matrix (`_classify_signals`)
was already Python-computed from fair value vs current price — never derived from the scout's
position field.

**Impact on vote consensus:** Scout vote becomes HOLD (abstain) in all cases. Bull and bear agents
remain the directional voices. Chartist and sentiment agents retain their directional votes.

---

## 2026-06-22 — `p3-counter-trend-rr-v22`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (STEP 4 counter-trend AVOID rule added)
- `core/risk_governor.py` (counter-trend R/R floor 2.5x enforced deterministically)
- `services/debate_prompts/manifest.json` (version → `2026-06-22-p3-counter-trend-rr-v22`)

### Changes

**`cio_judge.txt`** — STEP 4 AVOID rules extended.

Added explicit counter-trend AVOID rule:
> AVOID (counter-trend): ma200_context = BELOW AND R/R < 2.5.

Counter-trend setups have empirically lower win rates; the system previously only
applied the standard 1.3x/1.5x tier floor. The higher 2.5x floor aligns with the
lower expected win rate of below-MA200 entries.

**`core/risk_governor.py`** — Deterministic enforcement of counter-trend R/R floor.

Added `_COUNTER_TREND_RR_FLOOR = 2.5`. After detecting `counter_trend_setup`,
if `rr_ratio < 2.5` the governor appends `rr_too_low` (hard reject), preventing
`conditional_deployable` from masking an insufficient-R/R counter-trend setup.

---

## 2026-06-22 — `p2-chartist-stop-v21`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 4 stop-loss multiplier corrected)
- `services/debate_prompts/manifest.json` (version → `2026-06-22-p2-chartist-stop-v21`)

### Changes

**`chartist.txt`** — STEP 4 stop-loss reference aligned with Python envelope.

The chartist told the LLM to debate around a 1.5× ATR stop while the actual
executed stop uses 2.5× ATR (`REGIME_ATR_STOP_MULTIPLIER` in `utils/technicals.py`).
Bear R2 stress tests and bull stop-validity arguments were semantically disconnected
from the real stop. Final trade output was unaffected (CIO uses Python prices verbatim)
but debate quality suffered.

**Before:** `Standard stop = 1.5 x ATR(14) below EMA20`
**After:** `Standard stop = 2.5 x ATR(14) below EMA20 (3.0x in DEFENSIVE regime)`

---

## 2026-06-22 — `p1-catalyst-window-v20`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (Phase B catalyst bonus window widened)
- `services/debate_prompts/manifest.json` (version → `2026-06-22-p1-catalyst-window-v20`)

### Changes

**`cio_judge.txt`** — Phase B confidence checklist item updated.

Previously awarded `[+0.02]` only for catalysts within 30 days — creating a short-term bias inside a 1–3 month trading frame. A catalyst materializing in month 2 or 3 is equally valid for the stated horizon but received no bonus.

**Before:** `[+0.02] Specific catalyst confirmed within 30 days`
**After:** `[+0.02] Specific catalyst confirmed within 1–3 months`

---

## 2026-06-21 — `taskE-failpass-v19`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (STEP 3 FAIL/PASS conflict matrix relaxed)
- `schemas/debate.py` (`CIOVerdict.momentum_play` field + confidence cap)
- `services/debate_prompts/manifest.json` (version → `2026-06-21-taskE-failpass-v19`)

### Changes

**`cio_judge.txt`** — FAIL/PASS conflict resolution matrix (STEP 3) relaxed.

Previously required **both** Foreign Flow AND Volume breakout for a BUY. New logic:
- Volume breakout alone (volume_surge_ratio ≥ 1.5 AND return_5d_pct > 0) → Lean BUY at 50% size
- Volume breakout + strongly positive Foreign Flow → BUY at 75% size
- No volume breakout → HOLD

LLM instructed to set `"momentum_play": true` in JSON output for FAIL/PASS-to-BUY verdicts.

**`schemas/debate.py`** — Added `CIOVerdict.momentum_play: bool = False`.
Deterministic Python guardrail in `_derive_computed_fields`: when `momentum_play=True` and
`rating in (BUY, STRONG_BUY)`, confidence is capped at `min(confidence, 0.65)`.

**Motivation:** Requiring both foreign flow AND volume was too restrictive. Volume is the primary
gate; foreign flow amplifies size but should not block the entry entirely.

---

## 2026-06-20 — `fv2-fibonacci-v18`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 15 — Fibonacci Retracement ditambahkan)
- `services/debate_prompts/manifest.json` (version → `2026-06-20-fv2-fibonacci-v18`)

### Changes

**`chartist.txt`** — STEP 15 Fibonacci Retracement ditambahkan setelah STEP 14 (AVWAP).

Fields yang dibaca: `fib_context` (NEAR_23_6 | NEAR_38_2 | NEAR_50_0 | NEAR_61_8 | NEAR_78_6 |
ABOVE_SWING_HIGH | BELOW_SWING_LOW | BETWEEN_LEVELS | INSUFFICIENT_DATA), `fib_trend`
(UPTREND | DOWNTREND), `fib_swing_low`, `fib_swing_high`, `fib_38_2`, `fib_50_0`, `fib_61_8`,
`nearest_fib_label`, `price_to_nearest_fib_pct`. Di-compute oleh `compute_fibonacci_levels()`
di `utils/technicals.py`.

Logika: retracement dari swing high ke swing low — 38.2%/50%/61.8% adalah key support di UPTREND,
key resistance di DOWNTREND. ABOVE_SWING_HIGH = breakout (no retracement needed).
BELOW_SWING_LOW = setup invalidated.

---

## 2026-06-20 — `fv1-anchored-vwap-v17`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 14 — Anchored VWAP ditambahkan)
- `services/debate_prompts/manifest.json` (version → `2026-06-20-fv1-anchored-vwap-v17`)

### Changes

**`chartist.txt`** — STEP 14 ANCHORED VWAP ditambahkan sebelum blok CONSTRAINTS.

Field yang dibaca: `avwap`, `avwap_position` (ABOVE_AVWAP | AT_AVWAP | BELOW_AVWAP | INSUFFICIENT_DATA),
`price_to_avwap_pct`, `avwap_anchor_bars_ago`. Semua di-compute oleh `compute_anchored_vwap()`
di `utils/technicals.py` dan di-feed ke `tech_indicators` di `_chartist_node` sebelum LLM dipanggil.

Logika: ABOVE = buyers since swing low in profit, AVWAP = dynamic support.
BELOW = semua buyer underwater, AVWAP = overhead resistance.

---

## 2026-06-20 — `s12-cio-dead-code-revert-v16`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (VALUATION DISAGREEMENT CHECK section removed)
- `services/debate_prompts/manifest.json` (version → `2026-06-20-s12-cio-dead-code-revert-v16`)

### Changes

**`cio_judge.txt`** — VALUATION DISAGREEMENT CHECK section dihapus (antara STEP 1 dan STEP 2).

**Alasan**: Section tersebut menginstruksikan CIO judge untuk membaca field `valuation_disagreement`
dari "output metadata", tetapi field ini ditambahkan oleh `_guarded()` di `legacy.py` **setelah**
`chamber.run()` selesai — jadi tidak pernah masuk ke context CIO saat debat berjalan.
Field `valuation_disagreement` tetap dihitung dan tersimpan di result dict post-debate
(visible di JSON output dan warning log), tetapi CIO judge tidak dapat menggunakannya secara real-time.

---

## 2026-06-19 — `s12-valuation-disagreement-v15`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (VALUATION DISAGREEMENT CHECK section added)
- `services/debate_prompts/manifest.json` (version → `2026-06-19-s12-valuation-disagreement-v15`)
- `services/fair_value_calculator.py` (`check_valuation_disagreement()` added)
- `core/orchestrator/legacy.py` (`run_batch_debates()` now accepts `candidates_by_ticker`,
  post-debate disagreement annotation added to result dict)

### Changes

**`cio_judge.txt`** — VALUATION DISAGREEMENT CHECK section inserted between STEP 1 and STEP 2:
- When `valuation_disagreement = SIGNIFICANT`, CIO must cite both FV figures and explain
  which it trusts and why.
- Mining/energy guidance: Graham FV overestimates at EPS-cycle peak → prefer debate FV.
- Bank guidance: two models use different bases → state basis explicitly.

---

## 2026-06-19 — `s10-volume-profile-v14`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 13 added: Volume Profile POC/HVN/LVN)
- `services/debate_prompts/manifest.json` (version → `2026-06-19-s10-volume-profile-v14`)
- `utils/technicals.py` (Task 20: `compute_volume_profile`)
- `utils/quality_checks.py` (Task 30: `check_verdict_quality` — 7-point narrative/structural checker)
- `services/debate_chamber.py` (Task 20 try/except block in `_chartist_node`)
- `tests/test_technicals.py` (7 tests for `compute_volume_profile`)
- `tests/test_output_quality.py` (8 quality gate tests for `CIOVerdict`)

### Changes

**`chartist.txt`** — STEP 13 added before CONSTRAINTS:
- Reads `poc`, `price_vs_poc`, `poc_distance_pct`, `hvn_levels`, `lvn_levels`.
- ABOVE_POC/BELOW_POC: notes POC as support/resistance; cites % distance.
- AT_POC: "directional decision" commentary.
- HVN: flags as key institutional support/resistance nodes.
- LVN: warns against limit orders inside thin-volume zones.
- INSUFFICIENT_DATA → silent (skips commentary).

**`utils/technicals.py`** — Task 20 (`compute_volume_profile`):
- Typical-price bucketing: each bar's volume attributed to (H+L+C)/3.
- 20 equal-width bins over the rolling 60-day price range.
- POC = bucket with maximum cumulative volume.
- HVN = bins at or above 70th percentile (excluding POC), nearest 3 returned.
- LVN = bins at or below 30th percentile, nearest 2 returned.
- Zero-range guard: when all bars at same price → POC=close, AT_POC, no HVN/LVN.

**`utils/quality_checks.py`** — Task 30 (`check_verdict_quality`):
- 7-point advisory checker: weighted_reasoning, critical_risk_factor, key_risks,
  key_catalysts (BUY only), summary, risk_reward_ratio computable, entry_price_range set.
- Returns list[str] of issue descriptions; empty = all gates pass.

---

## 2026-06-19 — `s9-vwap-flag-timetable-v13`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 10–12 added: VWAP, flag pattern, time-of-day)
- `utils/technicals.py` (Task 19: `compute_vwap`; Task 25: `detect_flag_pattern`; Task 26: `get_time_of_day_signal`)
- `services/debate_chamber.py` (imports + 3 new try/except blocks in `_chartist_node`)

### Changes

**`chartist.txt`** — three new steps added before CONSTRAINTS:
- STEP 10 VWAP: reads `vwap`, `vwap_position`; outputs "VWAP: Rp X,XXX | Price X% above/below VWAP."
- STEP 11 FLAG PATTERN: reads `flag_pattern`, `flag_confidence`, `pole_pct`; NONE → silent.
- STEP 12 TIME-OF-DAY: reads `idx_session`, `entry_window`; advisory only, does not change directional signal.

**`utils/technicals.py`** — Task 19 (`compute_vwap`):
- Rolling 20-day VWAP on daily bars; typical price = (H+L+C)/3; zero-volume bars excluded.
- Positions: ABOVE_VWAP (>+1%), BELOW_VWAP (<-1%), AT_VWAP (±1%), INSUFFICIENT_DATA (<20 bars).

**`utils/technicals.py`** — Task 25 (`detect_flag_pattern`):
- Requires ≥15 bars; pole ≥5% directional move; flag range <5% of flag mean.
- HIGH confidence when flag vol/pole vol <0.8; returns BULL_FLAG / BEAR_FLAG / NONE.

**`utils/technicals.py`** — Task 26 (`get_time_of_day_signal`):
- Injectable clock (`now: datetime | None`); WIB = UTC+7; maps time to IDX session + OPTIMAL/SUBOPTIMAL/AVOID.
- OPTIMAL: SESSION_1 (09:30–11:00) and SESSION_2 (14:00–15:00).
- Note: Friday early close not modelled (varies per week).

**`services/debate_chamber.py`** — `_chartist_node` wiring:
- Tasks 19+25 inside OHLCV try-block; Task 26 outside OHLCV block (runs even when OHLCV unavailable).

---

## 2026-06-18 — `s8-ev-ebitda-peer-compare-v12`

**Files changed:**
- `services/debate_prompts/fundamental_scout.txt` (STEP 3 extended for EV/EBITDA + sector peer context)
- `services/fair_value_calculator.py` (Task 24: `ev_ebitda_current`, `fair_value_ev_ebitda()`, mining weights; Task 27: `SECTOR_MEDIAN_PROFILES`, `build_sector_comparison()`)

### Changes

**`fundamental_scout.txt`** — STEP 3 SUPPORT METRICS extended:
- Mining/energy stocks: scout reads EV/EBITDA Band result from the FAIR VALUE REPORT.
- All sectors: scout reads SECTOR PEER CONTEXT block, states Above/In Line/Below Avg per metric.

**`fair_value_calculator.py`** — Task 24 (EV/EBITDA):
- `KeyStats.ev_ebitda_current`: new optional float; populated from `"EV to EBITDA (TTM)"` (confirmed Stockbit field name from worktree `fundamental_analyser.py`).
- `FairValueCalculator.fair_value_ev_ebitda()`: mining sector only; formula `price × (5.5 / current)`; sanity bounds 0.3×–3×.
- `SECTOR_WEIGHTS["mining"]` updated from `{pe:0.60, pb:0.30, ddm:0.10}` to `{pe:0.35, pb:0.20, ddm:0.05, ev_ebitda:0.40}` (sum=1.0 ✓).
- `_MINING_EV_EBITDA_TARGET = 5.5` (conservative IDX median).
- `fair_value_weighted()` calls `fair_value_ev_ebitda()`; confidence threshold updated to `n >= 3`.

**`fair_value_calculator.py`** — Task 27 (Sector Peer Comparison):
- `SECTOR_MEDIAN_PROFILES`: static IDX sector medians (pe, pb, roe, net_margin) for bank/mining/consumer/property/default.
- `build_sector_comparison()`: compares P/E, P/BV, ROE, Net Margin to sector median; appended to `build_report()`.

---

## 2026-06-18 — `s7-foreign-flow-v11`

**Files changed:**
- `services/debate_prompts/cio_judge.txt` (FOREIGN FLOW CONTEXT block added)
- `providers/idx_foreign_flow.py` (new file — `ForeignFlowSnapshot`, `fetch_foreign_flow`)
- `services/debate_chamber.py` (`_synthesizer_node` fetches foreign flow + passes 3 fields to context pack)
- `services/context_pack_builder.py` (`net_foreign_flow_m`, `foreign_vol_pct`, `is_net_foreign_buy` added to tier2)

### Changes

**`cio_judge.txt`** — Added FOREIGN FLOW CONTEXT block between STEP 2 and STEP 3:
- Defines `net_foreign_flow_m`, `foreign_vol_pct`, `is_net_foreign_buy` fields so the CIO judge knows where to read them.
- Defines "strongly positive" (is_net_foreign_buy=true AND net_foreign_flow_m >= 500) and "strongly negative" (net_foreign_flow_m <= -500) thresholds used in the FAIL/PASS conflict resolution rule.
- INSUFFICIENT_DATA fallback → treat as neutral.

**`providers/idx_foreign_flow.py`** (NEW) — Task 16:
- `ForeignFlowSnapshot` dataclass: `net_foreign_flow_m` (IDR millions), `foreign_buy_m`, `foreign_sell_m`, `foreign_vol_pct` (% of total volume), `net_foreign_vol` (shares), `is_net_foreign_buy` (bool), `as_of_date`.
- Data source: Stockbit findata-view/foreign-domestic endpoint, period=PERIOD_RANGE_1D.
- Returns `_empty()` snapshot (all-None) on any failure — callers never need a guard.

**`debate_chamber.py`** — `_synthesizer_node` wires foreign flow:
- Calls `fetch_foreign_flow` via `asyncio.to_thread` (same pattern as `_fetch_url`).
- Graceful fallback to `_empty` on exception (auth failures in tests are silent).
- Adds `stockbit_foreign_flow` to `sources` and `source_timestamps` only if data is present.
- Injects `net_foreign_flow_m`, `foreign_vol_pct`, `is_net_foreign_buy` into `build_context_pack` raw_data dict.

**`context_pack_builder.py`** — three new tier2 fields:
- `net_foreign_flow_m`, `foreign_vol_pct`, `is_net_foreign_buy`.

## 2026-06-18 — `s6-insider-sell-post-earnings-v10`

**Files changed:**
- `services/debate_prompts/fundamental_scout.txt` (Step 6 added)
- `services/news_fetcher.py` (`INSIDER_SELL`, `POST_EARNINGS` tags + detection)
- `services/context_pack_builder.py` (`insider_selling_flag`, `post_earnings_flag` added to tier2)
- `services/debate_chamber.py` (sentiment_scout threads new flags to metadata + synthesizer)

### Changes

**`fundamental_scout.txt`** — Added STEP 6 (Earnings & Insider Signal):
- Task 18: POST-EARNINGS WINDOW flag → drift direction assessment + confidence -0.10 if >5% already moved.
- Task 17: INSIDER SELLING DETECTED flag → mandatory cap at 0.55 confidence + BULLISH block unless independently confirmed.
- Step fires only when the relevant flag string is present in the news/sentiment context.

**`news_fetcher.py`** — Task 17 + 18 event detection:
- `INSIDER_SELLING_KEYWORDS` (Indonesian + English): "jual saham", "divestasi", "insider selling", etc.
- `POST_EARNINGS_KEYWORDS`: "laba bersih", "laporan keuangan", "earnings", "kuartal", etc.
- `NewsEventTag.INSIDER_SELL` and `NewsEventTag.POST_EARNINGS` added.
- `NewsItem.is_insider_selling` + `NewsItem.is_post_earnings` boolean fields.
- `NewsBundle.has_insider_selling` + `NewsBundle.has_post_earnings` aggregate flags.
- `bundle_to_prompt_string()` renders "INSIDER SELLING DETECTED" / "POST-EARNINGS WINDOW" lines.

**`context_pack_builder.py`** — `insider_selling_flag` and `post_earnings_flag` in tier2 (not droppable).

**`debate_chamber.py`** — Threads `has_insider_selling` and `has_post_earnings` from news_bundle → metadata → synthesizer raw_data.

## 2026-06-18 — `s4-lq45-t2-circuit-breaker-anti-avg-down-v9`

**Files changed:**
- `services/debate_prompts/devils_advocate.txt` (Challenge 3 anti-averaging down added)
- `services/debate_prompts/cio_judge.txt` (IDX Market Mechanics header + anti-averaging down override + PHASE B penalties)
- `config/rr_tiers.yaml` (lq45_tickers list added)
- `utils/trade_math.py` (`_load_lq45_tickers` + `is_lq45_ticker` added)
- `core/quant_filter/pipeline.py` (`is_lq45` added to `_analyze_ticker` return dict)
- `services/context_pack_builder.py` (`is_lq45` added to tier2 + `_collect_priority_fields`)

### Changes

**`cio_judge.txt`** — Added IDX MARKET MECHANICS section before STEP 0:
- Task 14: T+2 settlement note (swing trades unaffected; intraday flips flagged).
- Task 14: Trading Hours WIB — breakout = Session I open; mean-reversion = Session II preferred.
- Task 15: IHSG Circuit Breaker — -8%/-15%/-20% halt levels. If regime=DEFENSIVE, add warning note; if DEFENSIVE + R/R < 2.0, prefer AVOID.
- Task 22: ANTI-AVERAGING DOWN OVERRIDE in STEP 4 — if ma200_context=BELOW + DA raised averaging-down challenge → force partial_exit_t1_pct=0.75.
- Task 22: PHASE B penalty [-0.02] for averaging-down setup.
- Task 21: PHASE B penalty [-0.01] for is_lq45=True + regime=DEFENSIVE.

**`devils_advocate.txt`** — Added CHALLENGE 3 (conditional):
- Anti-averaging down check: applies when ma200_context=BELOW AND price ≥10% below recent high.
- Raises: has original thesis changed? If not, recommend waiting for MA200 reclaim.

**`rr_tiers.yaml`** + **`trade_math.py`** + **`pipeline.py`** + **`context_pack_builder.py`** — LQ45 flag:
- 45-ticker LQ45 list added to YAML (Feb-Jul 2026 composition).
- `is_lq45_ticker(ticker)` helper in `trade_math.py` (cached).
- `is_lq45` bool added to `_analyze_ticker` return dict in `pipeline.py`.
- `is_lq45` added to tier2 in context_pack and extracted in `_collect_priority_fields`.

---

## 2026-06-18 — `s3-chartist-multitf-macd-patterns-exit-plan-v8`

**Files changed:**
- `services/debate_prompts/chartist.txt` (STEP 7/8/9 added)
- `services/debate_prompts/cio_judge.txt` (EXIT PLAN section added)
- `services/debate_chamber.py` (weekly/MACD/BB/candlestick/divergence/gap/compression wired into `tech_indicators`)
- `core/quant_filter/pipeline.py` (Tasks 10/11/12 wired into `_analyze_ticker` return dict)
- `utils/trade_math.py` (`compute_exit_plan` function added)

### Changes

**`chartist.txt`** — Added 3 new steps:
- STEP 7: Multi-Timeframe Context — reads `weekly_trend` (UPTREND/WEAK_UPTREND/DOWNTREND/INSUFFICIENT_DATA). DOWNTREND caps chartist signal at HOLD maximum, forces 50% position reduction.
- STEP 8: MACD Momentum — reads `macd_histogram_state` (4 states). Guides entry timing.
- STEP 9: Setup & Pattern Signals — reads `last_candle_pattern`, `pattern_type`, `bb_position`, `bb_squeeze`, `rsi_divergence`, `gap_type`, `compression_type`, `is_nr7`, `is_inside_bar`. All prose-style interpretation, no placeholder substitution.

**`cio_judge.txt`** — Added EXIT PLAN section instructing CIO to populate `partial_exit_t1_pct` and `partial_exit_trail_remainder`. Counter-trend (MA200 BELOW) raises T1 exit to 75%; DOWNTREND weekly forces full 100% T1 exit.

**`debate_chamber.py`** — `_chartist_node` extends `tech_indicators` with: weekly trend (separate `fetch_weekly_data` call), MACD, candlestick pattern, Bollinger bands, RSI divergence, gap type, and volatility compression. Each wrapped in its own try/except.

**`utils/trade_math.py`** — `compute_exit_plan()` computes T1/T2 gain percentages, trail trigger price, and exit note. Guards zero/negative risk and None t2_price.

---


## 2026-06-16 — `d2-sentiment-priority-fix-v7` (CODE-LEVEL)

**Files changed:**
- `services/debate_chamber.py` (`_sentiment_signal_from_payload`, `_normalise_position`)
- `tests/test_debate_chamber_reliability.py` (version assertion, companion to manifest bump)

### Problem (confirmed P1 — follow-up to d1-scout-position-fix-v6)

`_sentiment_signal_from_payload()` derived `raw_position` from an or-chain:
`payload.get("position") or payload.get("swing_signal") or payload.get("sentiment")`. The
current `sentiment.txt` schema has no `"position"` key (always None), and `swing_signal` is
always populated with a descriptive sentence per the prompt's own STEP 2d instruction, so
`swing_signal` won every time. `_normalise_position()` does an exact-token match against a
full sentence -> `"UNKNOWN"` -> falls through to the function's catch-all `position = "HOLD"`.
The real `"sentiment"` field (BULLISH/NEUTRAL/BEARISH/INSUFFICIENT_DATA) was never read.

Proven impact (empirical, direct call to the production function): a payload with
`sentiment: "BULLISH"` and a calm, non-contrarian `swing_signal` ("Trending with price, no
extreme bias detected") still returned `position: "HOLD"`. sentiment_specialist's vote in the
5-agent consensus count was decoupled from its own sentiment classification on effectively
every successful response.

### Changes

**`_sentiment_signal_from_payload()`** — or-chain reordered to
`sentiment -> position -> swing_signal`, so the current schema's real field is checked first;
`position` kept as a fallback for the legacy schema; `swing_signal` demoted to last resort.

**`_normalise_position()`** — added explicit `"INSUFFICIENT_DATA" -> "HOLD"` mapping
(previously relied on the caller's generic UNKNOWN->HOLD catch-all; now explicit for any
caller of this shared helper, not just the sentiment path).

**`tests/test_debate_chamber_reliability.py`** — version assertion updated to
`2026-06-16-d2-sentiment-priority-fix-v7` (companion to the manifest bump).

### Verification
Re-ran the exact bug-reproduction payload from this fix's investigation: `sentiment: "BULLISH"`
now resolves to `position: "BUY"` end-to-end through `_collect_agent_votes` (was `"HOLD"`
before this fix). `_normalise_position` confirmed: BULLISH->BUY, BEARISH->AVOID, NEUTRAL->HOLD,
INSUFFICIENT_DATA->HOLD.

### Tests
`tests/test_debate_chamber_reliability.py`: 83 passed, 0 failed. Note: this count includes
unrelated parallel changes present in the working tree at verification time (see session note
below) — the 4 additional tests beyond the prior 79-test baseline are not part of this fix.

### Note on parallel working-tree changes (not part of this fix, documented for traceability)
At verification time, `git status` showed uncommitted changes to files this fix never touched:
`core/orchestrator/legacy.py`, `core/risk_governor.py`, `schemas/debate.py`,
`services/debate_prompts/devils_advocate.txt`, `tests/test_cli_renderer_presentation.py`,
`tests/test_risk_governor.py`. These appear to be coherent, unrelated work (a preflight noise
gate, a `ConsensusMethod` Literal type fix adding the missing `"deadlock_hold"` value, a
devils_advocate ground-truth clarification) — not corruption, not reverted by this fix. Flagged
to the user directly rather than silently absorbed into this entry.

### Known follow-up (NOT done here, flagged)
This fix only corrects field PRIORITY in `_sentiment_signal_from_payload`. It does not address
whether `_POSITION_RE`'s `swing_signal` keyword alternation should be reconsidered now that
`sentiment` is the canonical field name — that regex is shared by every agent's prose-footer
parsing and was out of scope for this targeted fix.

---

## 2026-06-16 — `d1-scout-position-fix-v6` (PROMPT + CODE, test-only)

**Files changed:**
- `services/debate_prompts/chartist.txt` (P1 fix)
- `services/debate_prompts/fundamental_scout.txt` (P1 fix)
- `tests/test_debate_chamber_reliability.py` (version assertion, companion to manifest bump)

### Problem (confirmed P1 — audit finding D1, escalated from the p2-english-v5 read-only review)

`chartist.txt` and `fundamental_scout.txt` hardcoded a literal `Position: NEUTRAL` footer
regardless of the scout's actual analysis. `_collect_agent_votes()`
(`debate_chamber.py:1474-1491`) re-parses that exact text via `_extract_agent_signal()` ->
`_infer_position_from_text()` -> `_POSITION_RE` (`debate_chamber.py:967-970`, "NEUTRAL" is a
literal alternation member) -> `_normalise_position()` (`debate_chamber.py:982`, NEUTRAL ->
HOLD). Both scouts therefore contributed a permanent, content-independent HOLD vote into the
5-agent consensus count (`CONSENSUS_AGENT_COUNT = 5`, `debate_chamber.py:3086-3088`).

Proven impact: at most 3 of 5 agents (bull, bear, sentiment_specialist) could ever agree on a
non-HOLD position. Round-1 `ROUND1_CONSENSUS_THRESHOLD = 0.80` needs 4/5 -> BUY/AVOID
early-consensus at Round 1 was mathematically unreachable, independent of analysis quality.

### Root cause confirmation (Step 0 read before editing)

`_normalise_position()` (`debate_chamber.py:974-984`) already mapped `BULLISH -> BUY` and
`BEARISH -> AVOID` before this fix. No code change needed there — this is a prompt-only fix
plus one companion test-assertion update.

### Changes

**`chartist.txt` / `fundamental_scout.txt`** — OUTPUT FORMAT footer changed from hardcoded
`Position: NEUTRAL` to `Position: BULLISH | NEUTRAL | BEARISH` with explicit selection
criteria tied to each scout's own analysis above the footer (chartist: ma200_context / RSI /
price structure; fundamental_scout: valuation verdict UNDERVALUED / FAIRLY VALUED /
OVERVALUED).

**`tests/test_debate_chamber_reliability.py`** — version assertion updated to
`2026-06-16-d1-scout-position-fix-v6` (companion to the manifest bump, not a behavioral
change).

### Tests
`tests/test_debate_chamber_reliability.py`: 79 passed, 0 failed (same count as the prior
`p2-english-v5` baseline).

### Known open question (NOT resolved by this fix, flagged for a separate decision)
Whether `fundamental_scout`/`chartist` should be full voting agents (current design, now
correctly wired) versus evidence-only inputs excluded from `_collect_agent_votes` — like
`devils_advocate` already is via its `STRESS_TEST` -> `UNKNOWN` sentinel — is a separate
architectural question. The existing `calibration_weight` per agent (`debate_chamber.py:773`)
suggests the original design intent was real per-scout votes, which is what this fix restores.

---

## 2026-06-16 — `2026-06-16-p2-english-v5`

**Files changed:** All 12 prompt files in `services/debate_prompts/`

### P2 — Full English Rewrite + 10 Prompt-Writing Principles

**Scope:** Complete translation to English and structural rewrite of all 12 prompts applying:
1. Execution Order — explicit STEP-by-STEP GPS sequence in every file
2. No Forward Reference — ROLE and DATA SOURCE defined before tasks
3. Single Source of Truth — OUTPUT FORMAT at bottom; each constraint has one home
4. Explicit Conditional — IF/THEN/ELSE replacing implied branching throughout
5. Repeat Hard Constraints — DO NOT rules repeated at point of use
6. Explicit DO NOT — prohibitions stated explicitly, not implied
7. Consistent Notation — `->` for conditionals, `Rp` for prices, `0.xx` for numeric confidence
8. Output Format at Bottom — moved to last section in every file
9. Meta-Instructions Separate — CONSTRAINTS block grouped before OUTPUT FORMAT
10. Proportional — removed padding, duplicate rules, mixed-language comments

**File-specific changes:**
- `fundamental_scout.txt`: Translated FALLBACK and QUALITY CHECK from Indonesian;
  restructured as STEP 1-5 with explicit IF/THEN conditionals.
- `chartist.txt`: Translated MA200 matrix strings from Indonesian; explicit IF/THEN for
  all four ma200_context values; BELOW constraint repeated in TARGET PRICE step.
- `sentiment.txt`: Translated PRE-CHECK and RULES from Indonesian; STEP 1/2 structure
  with explicit data availability gate; OUTPUT FORMAT (JSON schema) at bottom.
- `devils_advocate.txt`: Translated full transaction cost section from Indonesian;
  CHALLENGE 1/2 structure with explicit IF/THEN cost verdicts.
- `bull_r1.txt`, `bear_r1.txt`: STEP 1 audit gate (IF/THEN) before STEP 2 thesis.
- `bull_r2.txt`, `bear_r2.txt`: STEP 1 DO NOT REPEAT gate before STEP 2 counter-argument.
- `consensus.txt`, `state_cleaner.txt`, `agent_signal.txt`: ROLE/TASK/CONSTRAINTS/OUTPUT
  structure applied; explicit DO NOT constraints added.
- `cio_judge.txt`: Renamed confidence sub-steps STEP A/B/C -> PHASE A/B/C (avoids
  numbering conflict with main STEP 0-6); added section separators; removed leftover
  `Position: NEUTRAL / Agent Confidence: HIGH|MEDIUM|LOW` footer (CIO is JSON-only);
  corrected CAP APPLICATION ORDER — prior version placed DA penalty after hard caps but
  said "(before hard caps)" — contradiction fixed: order is now base -> disagreement
  penalty -> DA penalty -> ordered caps -> hard caps -> anti-anchor check.

### Tests
`tests/test_debate_chamber_reliability.py`: version assertion updated to `2026-06-16-p2-english-v5`.

## 2026-06-03 — `momentum-rr-override-v1`

**File changed:** `services/debate_prompts/cio_judge.txt`

### Problem
CIO Judge had a hard rule: `Price > Fair Value → AVOID`, applied in both STEP 1
and STEP 4 regardless of R/R ratio. This caused DSSA (run 2026-06-01) to be
rejected despite a 15.82x R/R setup (entry Rp 492, target Rp 1,030, stop Rp 458).
The Graham Number fair value (Rp 304) killed a valid momentum trade.

### Root Cause
Graham Number is calibrated for value investing, not swing/momentum plays.
Stocks like DSSA/BREN/CUAN trade at structural premiums to Graham FV — applying
it as a hard AVOID gate discards setups with extreme asymmetric payoff.

### Changes

**STEP 1** — Replaced hard "strongly consider HOLD or AVOID" with R/R tiering:
- R/R < 2.0 → strongly consider AVOID (unchanged behavior)
- R/R 2.0–4.9 → strongly consider HOLD (new: was AVOID)
- R/R ≥ 5.0 → proceed to STEP 3 conflict resolution (new: was AVOID)

**STEP 4** — Added `BUY (Momentum)` rule and tightened AVOID condition:
- New: `Price > FV, R/R ≥ 5.0, Technical ✅, Volume breakout → BUY Momentum (50% size)`
- New: `Price > FV, R/R 2.0–4.9 → HOLD` (was grouped under AVOID)
- Changed: `AVOID` now requires `R/R < 2.0` when overvalued (was any overvaluation)

### Success Criteria
Re-run DSSA debate → expect HOLD or BUY (Momentum) instead of AVOID.
Existing value setups (R/R < 2.0, overvalued) should still get AVOID.

---

## 2026-06-03 — `momentum-rr-override-v2`

**File changed:** `services/debate_prompts/cio_judge.txt`

### Problem
v1 fix (STEP 1 + STEP 4) was insufficient. Even with STEP 1 passing R/R ≥ 5.0
cases to STEP 3, the STEP 3 matrix still had "Fund ❌ + Tech ❌ → AVOID" as an
absolute rule. DSSA (R/R 9.22x, Sentiment HOLD/non-bearish) still got AVOID.

### Change
**STEP 3** — Added R/R + Sentiment guard to "Fund ❌ + Tech ❌" case:
- IF R/R ≥ 5.0 AND Sentiment ≠ BEARISH → HOLD (Extreme Asymmetry Watchlist)
- OTHERWISE → AVOID (unchanged)

Sentiment guard prevents pump stocks with negative sentiment from benefiting.

### Success Criteria
- DSSA (R/R 9.22x, Sentiment HOLD) → HOLD not AVOID
- Stock with Fund ❌ + Tech ❌ + R/R 2.0 + any sentiment → still AVOID
- Stock with Fund ❌ + Tech ❌ + R/R 6.0 + Sentiment BEARISH → still AVOID

---

## 2026-06-03 — `momentum-rr-override-v3` (CODE-LEVEL, not prompt)

**File changed:** `services/debate_chamber.py`

### Problem
v1/v2 prompt fixes were correct but never took effect. After the CIO judge
LLM runs, `_apply_consensus_override` hard-forces the rating to the
`confidence_winner`'s position (Bear, AVOID @ 0.93) when no agent reaches the
60% vote threshold. The DSSA report literally shows the CIO reasoning
"normally R/R 9.22 would keep it on an asymmetry watchlist, but the mandatory
consensus directive says..." — i.e. the prompt logic fired and was then
overridden by code.

### Change
`_apply_consensus_override` (method == "confidence_winner"): when the winner
position is AVOID but R/R ≥ 5.0 and the sentiment specialist is non-bearish,
escalate to HOLD (Extreme Asymmetry Watchlist) and cap confidence at 0.55.

Two correctness fixes over the first v3 draft:
1. Sentiment guard checked `!= "BEARISH"`, but `_normalise_position` maps
   BEARISH/SELL → "AVOID", so the literal "BEARISH" never appeared and the
   guard was a no-op. Corrected to `!= "AVOID"`.
2. R/R was read from the LLM-echoed `risk_reward_ratio`. `_apply_envelope` now
   writes the canonical Python envelope R/R into the dict so the override keys
   off the deterministic number.

### Tests
`tests/test_debate_chamber_reliability.py`:
- `test_asymmetry_override_escalates_avoid_to_hold_on_high_rr`
- `test_asymmetry_override_blocked_by_bearish_sentiment`
- `test_asymmetry_override_blocked_by_low_rr`
Full file: 37 passed.

### Known limitation (FIXED in v4 below)
DSSA's R/R 9.22x is partly an artifact: when the Graham fair value is missing,
the envelope receives `fair_value≈0`, so the FV-blend target ceiling is skipped
and the target runs up to a recent pre-crash high (Rp 1,030), inflating R/R.

---

## 2026-06-03 — `momentum-rr-override-v4` (CODE-LEVEL, not prompt)

**Files changed:** `services/debate_chamber.py`, `tests/test_debate_chamber_reliability.py`

### Root cause (confirmed empirically)
`_compute_trade_envelope(current_price=615, fair_value, tech)` with DSSA inputs:
- `fair_value=304` → target Rp 665 (+9.9%), **R/R 1.11x**
- `fair_value=0/None` → target Rp 1,030 (+70%), **R/R 9.22x**

`build_fair_value_report()` returned `None` for DSSA (Graham uncomputable), so
`state["fair_value_estimate"]` was None and the envelope ran the FV-less path →
R/R 9.22x. The `304` + "(FV Blend)" shown in the verdict come from the separate
RAG/LLM path → the verdict was internally inconsistent. So R/R-as-a-gate (v3)
was fragile: it fired on an artifact, not a real setup.

### Changes
**Part 1 — realistic R/R (`_compute_trade_envelope`):**
- New `MAX_TARGET_RETURN_NO_FV = 0.15`. When `fair_value` is missing/≤0, cap the
  target at `entry_high × 1.15` (basis tag "(No-FV Cap)") so resistance levels
  can't inflate R/R. DSSA FV-less R/R now 2.0x (was 9.22x); FV-anchored path
  (1.11x) untouched.
- Fixed a latent `None > 0` crash in the returned `fair_value` field.

**Part 2 — momentum-based watchlist (`_apply_consensus_override`):**
- Replaced the `R/R ≥ 5.0` escalation trigger with a momentum gate. A
  confidence_winner of AVOID escalates to HOLD only when **all** hold:
  value-driven AVOID (overvalued or no FV anchor) **AND** a volume-confirmed
  breakout (`volume_surge_ratio ≥ VOL_SURGE_THRESHOLD=1.5` **AND**
  `return_5d_pct ≥ MOMENTUM_RETURN_THRESHOLD=5.0`) **AND** sentiment non-bearish.
- Added `volume_surge_ratio` and `return_5d_pct` to the chartist's
  `technical_indicators` (computed from raw OHLCV; MA-based signals miss a
  single-day surge on a name still below its MAs).

### Tests (38 passed)
- `test_momentum_override_escalates_avoid_to_hold`
- `test_momentum_override_blocked_by_bearish_sentiment`
- `test_momentum_override_blocked_without_volume_breakout`
- `test_momentum_override_blocked_when_not_overvalued`

### Important behavioural note
The gate is now **data-driven**. DSSA shows HOLD only if its loaded data carries
the volume-confirmed up-move. The cached run used June-1 data (pre-ARA); if DSSA
was crashing into June 1, `return_5d_pct` is negative → momentum gate → AVOID,
which is the honest call (the June-2 ARA surge is not in the data). Thresholds
are named constants for tuning once real numbers are observed.

### Follow-up (not done — flagged)
The CIO prompt (`cio_judge.txt`) still contains `R/R ≥ 5.0 → BUY (Momentum)` /
asymmetry-watchlist language (STEP 1/3/4). After Part 1, R/R can no longer reach
5.0 for overvalued/FV-less names, so those branches are effectively inert, but
the prompt text is now inconsistent with the momentum-based code path. Cleaning
it up needs a prompt_version bump + the version-assertion test update.

---

## 2026-06-03 — `sentiment-llm-news-v1` (CODE-LEVEL, no prompt_version bump)

**Files changed:** `services/debate_chamber.py`,
`tests/test_debate_chamber_reliability.py`, `tests/test_sentiment_node_data_volume.py`

### Problem (from the sentiment audit)
News sentiment was scored by a keyword lexicon (`news_fetcher.py`):
- 1 keyword = ±1.00 saturation; no negation; no "ARA"/limit-up; ticker
  false-positives ("Bagi Cuan" matched ticker CUAN); index round-ups that merely
  list the ticker drove a "POSITIVE" stock sentiment (price echo / circular).
- `overall_sentiment` and `confidence_adjustment` could contradict (BREN: shown
  POSITIVE but −0.20 because one macro "melemah" headline tripped the breaking
  penalty).

### Approach — reuse the existing LLM (no new API call)
The sentiment-specialist LLM already runs per debate on Stockbit social posts.
Feed it the recent news headlines too and have it judge them; demote the keyword
scorer to a fallback.

### Changes
- Output schema (`SENTIMENT_JSON_RESPONSE_FORMAT`) gains a `news_sentiment` field.
- New `SENTIMENT_NEWS_INSTRUCTION` constant: round-ups → NEUTRAL, ARA/limit-up →
  POSITIVE, suspensi/delisting/fraud → NEGATIVE, apply negation, ignore
  common-word ticker matches (e.g. "cuan").
- `_news_headlines_for_llm()` formats raw titles (no keyword labels) and is
  appended to the existing LLM Human message (NewsFetcher cache avoids a 2nd fetch).
- `_news_context_for_state(..., llm_news_sentiment=…)` derives BOTH
  `news_overall_sentiment` and `news_confidence_adjustment` from the LLM label via
  `_news_adjustment_from_sentiment()` → they can no longer contradict.

### Design decisions
- **D1** — `news_sentiment` is SEPARATE from the social vote (which drives the
  debate + the v4 momentum gate). Protects v4; the social vote is untouched.
- **D2** — social < 5 posts → LLM bails to INSUFFICIENT_DATA, news falls back to
  the keyword scorer. Hot stocks (the target) have ≥5 posts. Documented limitation.
- **D3** — adjustment map: POSITIVE +0.05 / NEGATIVE −0.10 (−0.20 if breaking) /
  NEUTRAL 0. Single source ⇒ overall ≡ adjustment.
- No `prompt_version` bump: the change is code constants + node logic, not a
  `debate_prompts/*.txt` edit, so the registry pack is unchanged.

### Tests (82 passed across the 3 files)
- `test_news_adjustment_from_sentiment_is_consistent`
- `test_news_context_llm_sentiment_overrides_keyword` (keyword POSITIVE → LLM
  NEGATIVE wins, overall≡adjustment — proves the BREN/CUAN contradiction is gone)
- `test_news_context_falls_back_to_keyword_when_no_llm_sentiment`
- Updated the sentiment-node fixture to mock `_news_headlines_for_llm`.

### Known limitations / follow-ups
- The 4 LLM-judgment criteria (round-up→NEUTRAL, ARA→POSITIVE, "cuan" not matched,
  suspensi→NEGATIVE) are prompt behaviours — verified via a live flash call, not
  unit tests.
- The `news_brief` shown to agents still carries the per-item keyword `[POSITIVE]`
  tags; only the overall sentiment + adjustment are LLM-driven. Minor; could
  regenerate the brief later.
- D2 couples news judgment to social volume; decouple later if needed.

---

## 2026-06-11 — `rr-sanity-v1` (CODE-LEVEL, no prompt_version bump)

**Files changed:** `services/debate_chamber.py`, `core/risk_governor.py`,
`tests/test_debate_chamber_reliability.py`, `tests/test_risk_governor.py`

### Problem (run 2026-06-11, INDO/NZIA)
- INDO: agents voted HOLD 4/5 + AVOID 1/5 (zero BUY) yet shipped **BUY @ 0.66,
  target +133.9%, R/R 22.3x**, ranked #1 with trade conviction 0.83.
- Three compounding mechanisms:
  1. The envelope "FV ceiling" was a **blend** `(target + FV) / 2` — with a far
     pre-crash 52w high it landed **above FV itself** (INDO: (519+253)/2 = 386
     vs FV 253). And when FV sits above the resistance target (NZIA: FV 417 >
     52w 316) no ceiling fired at all → +78% target, R/R 11.75x.
  2. `_apply_consensus_override` had **no branch for `method == "voting"`** —
     the CIO LLM rating passed through unclamped, so a HOLD majority exited as
     BUY (the CIO even cited "R/R 22.30 extreme asymmetry" as validation).
  3. R/R > 5 only produced a conviction-scorer *warning*; the saturated R/R
     component (cap 5.0, weight 0.5) then **boosted** the ranking score.

### Changes
**1 — `_compute_trade_envelope`:** FV blend → hard ceiling (`min(target, FV)`,
basis "(FV Ceiling)"), plus `MAX_TARGET_RETURN_NO_FV` renamed
`MAX_TARGET_RETURN = 0.15` and applied **universally** (basis "(Swing Cap)"),
not only when FV is missing. INDO-shape target now ~entry_high x 1.15, R/R ~4.

**2 — `core/risk_governor.py`:** new `RR_IMPLAUSIBLE_CEILING = 5.0`; R/R above
it appends `rr_implausible`, which is in `HARD_REJECT_CODES` → status reject,
no sizing. Matches the existing "mencurigakan tinggi" warning threshold and
`CONVICTION_RR_NORMALIZATION_CAP`. Backstop for tight-stop geometries that
survive the envelope caps.

**3 — `_apply_consensus_override` (`method == "voting"`):** new
`RATING_BULLISHNESS_RANK` clamp — the CIO rating may be more bearish than the
voting consensus, never more bullish (STRONG_BUY→BUY under a BUY vote;
BUY→HOLD under a HOLD vote, confidence capped 0.55 mirroring soft_hold).
Unknown ratings (INSUFFICIENT_DATA) pass through unchanged.

### Tests (610 passed full suite)
- `test_trade_envelope_fair_value_is_hard_ceiling_not_blend`
- `test_trade_envelope_swing_cap_applies_even_with_fair_value_above_resistance`
- `test_voting_override_clamps_cio_buy_to_hold_majority`
- `test_voting_override_keeps_more_bearish_cio_rating`
- `test_voting_override_clamps_strong_buy_to_buy_majority`
- `test_implausible_rr_is_hard_rejected` / `test_high_but_plausible_rr_stays_deployable`

### Known interaction
`momentum-rr-override-v1/v2` prompt language ("R/R >= 5.0 → BUY Momentum") is
now doubly inert: the envelope caps keep computed R/R below 5 for far-target
shapes, and the governor hard-rejects anything still above it. The prompt
cleanup flagged in v4 remains open.

---

## 2026-06-11 — `rr-sanity-v2` (CODE-LEVEL, no prompt_version bump)

**Files changed:** `services/fair_value_calculator.py`, `services/debate_chamber.py`,
`core/orchestrator/legacy.py`, `tests/test_fair_value_calculator.py`,
`tests/test_orchestrator_realized_scoring.py`

Continuation of `rr-sanity-v1` — items 4 and 5 of the same INDO/NZIA audit.

### Part 1 — Fair-value data-quality gate (`build_fair_value_payload`)
A fair value built on thin/broken inputs anchored the whole bull case (NZIA:
1/3 methods valid yet "FV Rp 417 vs spot Rp 177" became the BUY catalyst;
INDO: net margin 131% — net income > revenue — only flagged in prose as
NEEDS_RECONCILIATION). New deterministic gate after the weighted calc:

- `confidence == "LOW"` (fewer than 2 valid methods) → reason `fv_methods_lt_2`
- `stats.net_margin > 1.0` (post-normalisation ⇔ margin > 100%) → reason
  `net_margin_gt_100pct`

On trip: `fair_value`/`base`/`low`/`high`/`range_pct` → None,
`risk_overvalued` → False, `valuation_verdict` → `QUALITY_REJECTED`, and the
report text gains a "FAIR VALUE QUALITY GATE" warning so scouts stop quoting
the FV as fact. Per-method estimates stay visible in the report. Downstream
the envelope then runs FV-less → universal Swing Cap (rr-sanity-v1) applies.
Consumers: `_fundamental_node` (debate) and `single_agent_analyzer` both go
through this choke-point; no changes needed there beyond suppressing the
raw-JSON parse-failure log when the gate (not a parse failure) nulled the FV.

Known pre-existing quirks (NOT fixed, out of scope):
- `extract_keystats` Strategy B (legacy fallback) clobbers `net_margin` to 0.0
  when EPS/BVPS are absent — the margin signal only survives Strategy A.
- A decimal-format margin > 1.0 from a legacy source gets divided twice
  (1.31 → 0.0131) by the `> 1.0` normalisation.

### Part 2 — Conviction R/R component is now a tent (`_rr_component_score`)
Old: `rr_score = min(rr / cap, 1.0)` — monotonic, so INDO's artifact R/R
22.3x saturated at 1.0 and (at weight 0.5) pushed conviction to exactly 0.83:
the most suspicious setup ranked #1. New tent, parameterised by the existing
regime-tunable `rr_normalization_cap`:

- rise: 0 → 1.0 over [0, 0.6×cap]
- plateau: 1.0 on [0.6×cap, 0.8×cap]  (3.0–4.0 at default cap 5.0)
- fall: 1.0 → 0.0 over [0.8×cap, cap]; 0.0 at and beyond cap

Regime semantics preserved: DEFENSIVE/HIGH cap 4.0 → peak 2.4–3.2, zero at 4;
LOW cap 6.0 → peak 3.6–4.8. `_conviction_breakdown_row` now reuses the same
helper so the report breakdown matches the actual score (was an independent
copy of the old ramp). The >5x/>3.5x warning strings are unchanged.

INDO regression: conviction 0.83 → 0.33 (0.5×0.66 + 0.5×0.0).

### Tests (617 passed full suite)
- `test_quality_gate_rejects_single_method_fair_value`
- `test_quality_gate_rejects_margin_above_100_percent`
- `test_quality_gate_passes_two_methods_with_sane_margin`
- `test_rr_component_is_zero_at_implausible_rr`
- `test_rr_component_peaks_on_plateau`
- `test_rr_component_declines_past_plateau`
- `test_rr_component_still_rises_below_plateau`

---

## 2026-06-11 — `rr-sanity-v3` (CODE-LEVEL, review fixes)

**Files changed:** `core/orchestrator/legacy.py`, `services/debate_chamber.py`,
`tests/test_orchestrator_realized_scoring.py`, `tests/test_debate_chamber_reliability.py`

Fixes for the CONFIRMED findings of the deep review of rr-sanity-v1/v2.

### 1 — Tent zero-point anchored to the governor ceiling
`_rr_component_score` previously fell to 0.0 at `rr_normalization_cap`, which
diverged from the governor in both directions: LOW regime (cap 6.0) gave
positive conviction to R/R 5.0–5.9 that `RR_IMPLAUSIBLE_CEILING=5.0` hard-
rejects, and DEFENSIVE/HIGH (cap 4.0) zeroed R/R ≥ 4.0 that the governor still
accepts (max conviction 0.50 < DEFENSIVE min_conviction 0.70 → silent
exclusion). The fall now always ends at `RR_IMPLAUSIBLE_CEILING` (imported
from `core.risk_governor`); the plateau stays regime-scaled (0.6–0.8 × cap).
Default cap 5.0 behaviour is unchanged.

Boundary fix (review follow-up): the governor reject comparison is `>=` so an
R/R of exactly 5.0 — which the tent scores 0.0 — is also rejected; previously
`>` let the exact boundary pass the governor with a zeroed score component.

### 2 — Governor hard-rejects excluded from top_n
`select_top_n` now skips entries with `risk_governor.status == "reject"`
(annotated per-result during the batch loop), so a rejected setup can no
longer occupy a ranked slot while the same report shows actionability=reject.
Soft holds (wait_for_pullback / watchlist_only / conditional) still rank.

### 3 — Voting clamp hardening (`_apply_consensus_override`)
- CIO rating is space-normalised (`.replace(" ", "_")`, mirroring
  `risk_governor._clean_rating`) so variants like "STRONG BUY" cannot dodge
  the rank lookup and bypass the clamp into the Pydantic parse-fallback.
- Falsy-zero fix: `or 0.55` → `or 0.0` — a legitimate 0.0 confidence is no
  longer inflated to the HOLD cap. (Same latent pattern exists pre-diff in the
  soft_hold branch `or 0.52` — NOT touched, out of scope.)

### 4 — Envelope fallback preserves provenance
The `target <= entry_high` tick fallback now APPENDS
"(Tick Increment Fallback)" to `target_basis` instead of overwriting it, so
the "(FV Ceiling)"/"(Swing Cap)" label that explains why the target collapsed
survives into the audit trail.

### 5 — Quality rejection propagates to shared rejection metadata
`_fundamental_node` now mirrors the RAG-rejection fields when
`fv_quality_rejected` is set: `metadata.fair_value_rejected=True`,
`valuation_gap="unverified"`, reason `fair_value_quality_rejected`. Report and
audit consumers (legacy.py valuation-gap row, report_formatter) treat both
rejection kinds identically.

### Deliberate non-fix (reviewed finding, decision documented)
The quality gate keeps `risk_overvalued=False` for quality-rejected FV — the
"overvalued" hard-reject intentionally does NOT fire off a garbage anchor in
either direction. Restoring it would resurrect the DSSA failure mode
(single-method Graham FV triggering AVOID on momentum names) that
momentum-rr-override v1–v4 spent four iterations removing. The cohort is now
visible via the `unverified` marker instead of silent.

### Tests (624 passed full suite)
- `test_rr_tent_zero_point_is_anchored_to_governor_ceiling`
- `test_rr_tent_does_not_zero_below_governor_ceiling_in_tight_regimes`
- `test_select_top_n_excludes_governor_rejected_entries`
- `test_voting_override_clamps_spaced_rating_variant`
- `test_voting_override_preserves_zero_confidence_on_clamp`
- `test_trade_envelope_tick_fallback_preserves_ceiling_provenance`
- `test_fundamental_node_propagates_quality_rejection_to_metadata`

---

## 2026-06-12 — `rr-implausible-cleanup-v1` (PROMPT-LEVEL)

**File changed:** `services/debate_prompts/cio_judge.txt`

### Problem
`cio_judge.txt` STEP 1 and STEP 3 still contained R/R ≥ 5.0 reasoning logic added
in `momentum-rr-override-v1/v2`. The Python governor hard-rejects R/R ≥ 5.0 as
implausible data (`RR_IMPLAUSIBLE_CEILING = 5.0`). The code-level v4 fix replaced
the R/R escalation gate with a momentum gate in `_apply_consensus_override`, but the
prompt text was left as a known follow-up (see `momentum-rr-override-v4` entry).

### Changes
**STEP 1** — R/R ≥ 5.0 branch changed from "Proceed to STEP 3" to "IMPLAUSIBLE — rate HOLD".

**STEP 3** — "Fund ❌ + Tech ❌ + R/R ≥ 5.0 → HOLD (Extreme Asymmetry Watchlist)"
replaced with "Fund ❌ + Tech ❌ → AVOID (R/R ≥ 5.0 is implausible data)".

### Success Criteria
`grep -n "R/R.*5\.0" services/debate_prompts/cio_judge.txt` → 0 matches

---

## 2026-06-12 — `exdate-gate-precomputed-v1` (PROMPT-LEVEL + CODE-LEVEL)

**Files changed:** `services/debate_prompts/cio_judge.txt`, `services/debate_chamber.py`

### Problem
STEP 0 of `cio_judge.txt` instructed the LLM to "calculate days since ExDate"
using the raw ExDate string from the Trade Envelope. LLM date arithmetic is
unreliable and produces silent failures when the date format is ambiguous or the
ExDate field is null.

### Changes
**`debate_chamber.py`** — New `_compute_exdate_gate(exdate_info)` module-level
function reads the `ExDateInfo` TypedDict (fields: `risk_tier`, `days_until_exdate`)
and emits a deterministic gate string: `EXDATE_GATE: AVOID`, `EXDATE_GATE: CAP_65`,
`EXDATE_GATE: MONITOR`, or `EXDATE_GATE: CLEAR`. The gate string is prepended to
`raw` in `_synthesizer_node` so the CIO sees it at the top of the brief.

**`cio_judge.txt` STEP 0** — Replaced date-arithmetic instructions with:
"Read the EXDATE_GATE line at the TOP of the brief and apply it exactly."
LLM no longer calculates days; it only pattern-matches the pre-computed label.

### Success Criteria
`_compute_exdate_gate({"risk_tier": "CRITICAL", "days_until_exdate": 5})` →
`"EXDATE_GATE: AVOID (ExDate in 5d — do not enter)"`

---

## 2026-06-12 — `bull-bear-citation-requirement-v1` (PROMPT-LEVEL)

**Files changed:** `services/debate_prompts/bull_r1.txt`, `services/debate_prompts/bear_r1.txt`

### Problem
Both R1 prompts required citing exact prices but had no structured minimum-citation floor.
Without it, analysts could repeat the same qualitative argument across R1/R2/R3 and the
multi-round debate would not converge on new evidence. Audit finding C3-F02.

### Changes
Both files: inserted a REQUIRED DATA CITATIONS block at the top (before ROUND 1 OBJECTIVE)
mandating 3 structured citations per round:
  1. One fundamental metric with its actual number
  2. One technical metric with its actual number
  3. One company-specific catalyst or risk factor (not generic market commentary)

PROHIBITED clauses added to block data-free assertions. Fallback: if brief lacks data for
3 citations, cap confidence <= 0.50 and declare HOLD/AVOID respectively.

R2 prompts (bull_r2.txt, bear_r2.txt) are unchanged.

### Success Criteria
Bull/Bear R1 LLM output cites at least 3 specific numbers from the brief before paragraphs.

---

## 2026-06-15 — `cio-rr-floor-and-hold-guard-v1` (PROMPT-LEVEL)

**File changed:** `services/debate_prompts/cio_judge.txt`

### Problem
`diag_consensus.py` (272 debates, 15 days) revealed two CIO bias bugs:

1. **R/R ≥ 5.0 IMPLAUSIBLE rule:** STEP 1 claimed "this value should not appear" and
   forced HOLD. But tight-stop setups (e.g. TPIA entry 1250, stop 1215, target 1437)
   produce R/R > 5.0 even after the swing cap. The governor hard-rejects these anyway —
   the prompt assertion was factually wrong and incorrectly rate-blocked the CIO.

2. **HOLD downgrade guard missing:** `voting HOLD → CIO AVOID = 46%` (124/272 debates).
   AVOID conditions (R/R < 2.0 alone, no clear catalyst) were triggering freely even when
   the full debate consensus reached HOLD. No guard prevented CIO from overriding HOLD.

### Changes

**STEP 1** — R/R ≥ 5.0 branch changed from "IMPLAUSIBLE — rate HOLD" to:
"High R/R setup. Verify consistency. Apply normal BUY/HOLD/AVOID rules. Do NOT auto-rate
HOLD solely based on R/R magnitude."

**STEP 3** — "(R/R ≥ 5.0 is treated as implausible data...)" changed to:
"(High R/R alone does not override two failing signals — check fundamentals independently)"

**STEP 4** — New `HOLD (downgrade guard)` bullet added before AVOID:
"IF debate consensus = HOLD AND R/R ≥ 1.5 AND no hard disqualifier → Preserve HOLD.
Do NOT downgrade to AVOID based on R/R < 2.0 alone.
Hard disqualifiers: EXDATE=AVOID, R/R < 1.0, price > 1.5× fair value."

### Note on governor interaction
R/R ≥ 5.0 setups are still hard-rejected by `core/risk_governor.py`
(`RR_IMPLAUSIBLE_CEILING = 5.0`). Fix 1 only affects the CIO rating text — these setups
do not reach portfolio sizing regardless of CIO rating.

### Success Criteria
- CIO downgrade rate (`voting HOLD → CIO AVOID`) drops from 46% toward ≤20%
- High-R/R setups (TPIA, INDO, MPOW) receive proper BUY/AVOID ratings instead of
  force-HOLD from IMPLAUSIBLE rule

---

## 2026-06-15 — `bear-hold-option-v1` (PROMPT-LEVEL)

**Files changed:** `services/debate_prompts/bear_r1.txt`, `services/debate_prompts/bear_r2.txt`

### Problem
Diagnostic showed bear agent voted AVOID in 100% of 378 debate records. Root cause:
footer line `Position: BEARISH` was a hardcoded literal, so LLM always echoed it verbatim.
Parser `_normalise_position("BEARISH")` maps to `"AVOID"`. bear_r1.txt:13 already had the
correct instruction ("declare HOLD, not AVOID" when data is insufficient) but that instruction
was overridden by the hardcoded footer.

### Changes
`bear_r1.txt:27` and `bear_r2.txt:17`:
```
# Before:
Position: BEARISH

# After:
Position: BEARISH | HOLD
```

### Success Criteria
Bear agent occasionally outputs `Position: HOLD` for stocks where data quality is too poor
to build a credible AVOID case. 100% AVOID rate should drop below 90%.

---

## 2026-06-15 — `p0-fix-v2` (PROMPT + CODE)

**Files changed:**
- `services/debate_prompts/sentiment.txt` (P0-1)
- `services/debate_prompts/bull_r1.txt`, `bull_r2.txt`, `bear_r1.txt`, `bear_r2.txt` (P0-2)
- `services/debate_prompts/devils_advocate.txt` (P0-3)
- `services/debate_prompts/agent_signal.txt` (scope comment added then removed in review)
- `services/debate_chamber.py` (deadlock_hold fix + review doc comment)

### Problems

**P0-1 (sentiment.txt):** RULES footer had prose instructions inconsistent with JSON schema
output, plus a trailing `Position: NEUTRAL / Agent Confidence` footer that doesn't belong in
a JSON-output agent.

**P0-2 (bull/bear debate prompts):** `bear_r1/r2.txt` still used `BEARISH | HOLD`. Aligned to
`BEARISH | NEUTRAL` so bear's non-AVOID position maps to HOLD via `_normalise_position`. Bull
updated to `BULLISH | NEUTRAL`. Footer label renamed "Agent Confidence" → "Debate Confidence".

**P0-3 (devils_advocate.txt):** DA `Position: BEARISH` made it a de-facto AVOID voter. Changed
to `Position: STRESS_TEST` (→ UNKNOWN in `_normalise_position`). DA is not in
`_collect_agent_votes` so this has no consensus impact. Numeric `Agent Confidence: 0.xx`
replaces `HIGH | MEDIUM | LOW`.

**Deadlock_hold (code):** `_evaluate_consensus_votes()` — genuine bull=BUY / bear=AVOID
deadlocks after MAX_DEBATE_ROUNDS now return `consensus_method="deadlock_hold"` with
HOLD starting point instead of letting bear win the confidence race (bear avg 0.74 effective
vs bull avg 0.64). `_apply_consensus_override` and `_format_consensus_directive` updated.

### Review findings (2026-06-15 post-session)

**Scope comment regression (fixed):** SCOPE RESTRICTION task added 19 `#` lines to
`agent_signal.txt`. `load_prompt_registry()` reads `.txt` files verbatim — no comment
stripping — so these lines shipped to the LLM inside bull/bear system messages, creating
contradictory instructions. Block removed; accurate Python comment added in
`debate_chamber.py` at the injection sites instead.

**P0-2 footer is overridden at runtime:** `AGENT_SIGNAL_PROMPT` is appended to debate nodes
(lines 3007/3052) and its MANDATORY instruction takes precedence over the `Debate Confidence`
footer in bull/bear prompts. LLMs output numeric `Agent Confidence: 0.xx` (required for
effective_confidence). The `Debate Confidence` label in debate prompt footers is informational
only. Separating qualitative debate confidence from numeric scout confidence would require
`_CONFIDENCE_RE` changes and is deferred as a design decision.

### Tests
`tests/test_debate_chamber_reliability.py`: 79 passed, 0 failed.
`test_consensus_round_three_uses_confidence_winner` → updated to assert `deadlock_hold`.
`test_confidence_winner_uses_effective_calibrated_confidence` → bull fixture changed to
`Position: HOLD` to avoid triggering deadlock_hold path.

---

## 2026-06-15 — `p1-fix-v3` (PROMPT-LEVEL)

**Files changed:**
- `services/debate_prompts/sentiment.txt` (P1-A: 3 edits)
- `services/debate_prompts/cio_judge.txt` (P1-A: 4 edits)
- `services/debate_prompts/fundamental_scout.txt` (P1-B: 1 edit)
- `services/debate_prompts/bear_r2.txt` (P1-C: 1 edit)
- `services/debate_prompts/bull_r2.txt` (P1-C: 1 edit)

### P1-A — Sentiment EXTREME Dead Code Fix

**Root cause:** `cio_judge.txt` Step 3 had a trigger `Any ✅ + Sentiment EXTREME → -0.10
confidence` but `sentiment.txt` never produced a field or value called "EXTREME" — the
condition could never fire. Three changes in `sentiment.txt` + four in `cio_judge.txt`:

- `sentiment.txt`: Added `"sentiment_intensity": null` to the insufficient-data short-circuit
  JSON response (A1).
- `sentiment.txt`: Expanded swing_signal bullet with enum definition for `sentiment_intensity`
  — five values: EXTREME_BULLISH, BULLISH, NEUTRAL, BEARISH, EXTREME_BEARISH (A2).
- `sentiment.txt`: Changed `% confidence estimate` to `decimal confidence 0.0–1.0` to match
  the CIO threshold check `>= 0.7` (A3).
- `cio_judge.txt`: Updated Step 3 EXTREME trigger to read `sentiment_intensity = "EXTREME_BULLISH"
  atau "EXTREME_BEARISH"` (A4).
- `cio_judge.txt`: Updated three "Sentiment X" references in Step B and ORDERED CAPS section
  to explicit field notation `sentiment.sentiment = "..."` (A5).

### P1-B — Fundamental Scout Fair Value Injection Fallback

**Root cause:** FAIR VALUE item assumed Python always injects "FAIR VALUE REPORT". No fallback
meant the scout would hallucinate numbers if the injection silently failed. Added an explicit
fallback: declare "DATA TIDAK TERSEDIA", cap confidence to 0.40, declare HOLD, and continue
items 2–5 with available data.

### P1-C — R2 Prompts Input Schema Declaration

**Root cause:** bear_r2.txt and bull_r2.txt instructed agents to attack the opponent's R1
argument but never declared which context variables hold that argument — silent failure if
pipeline wiring changes. Added INPUT CONTEXT block to both R2 prompts naming BULL_R1_OUTPUT,
BEAR_R1_OUTPUT, and TECHNICAL_SUMMARY, with explicit LOW confidence cap if any is missing.

### Tests
`tests/test_debate_chamber_reliability.py`: 79 passed, 0 failed.
