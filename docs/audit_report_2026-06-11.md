# IDX Swing-Trade Pipeline — Exhaustive Audit Report

> Historical snapshot: this report captures the pipeline state observed on
> 2026-06-11. It is retained for audit evidence and regression context, but it
> is not the current architecture contract. For current decision ownership, use
> `docs/architecture_decision_map.md`; for implemented cleanup status, use
> `docs/de_overengineering_execution_checklist_2026-07-03.md`.

**Date:** 2026-06-11  
**Auditor:** Principal Quant Engineer / AI Systems Architect  
**Standard:** Make No Mistake — assume real investment decisions  
**Pipeline run observed:** `scratch/report.md` (10 screened candidates), `output/TOP_3_SWING_TRADES.md` (1 stock debated in full pipeline, 0 BUY eligible); standalone `uv run idx debate DMAS MPMX PSAB MAPI BMRI` ran all 5 successfully (22m 55s) — all HOLD/AVOID, 0 BUY  
**Realized backtest data:** `output/backtest/backtest_memory.jsonl` — 80 closed trades, **1W / 79L** (1.25% win rate), avg PnL −3.29%

---

## PHASE 0 — System Map

```
┌─────────────────────────────────────────────────────────────────────┐
│                     DATA INGESTION LAYER                            │
│  XLSX scraping (legacy main.py) ──► quant_filter reads latest XLSX │
│  yfinance OHLCV (120d, auto_adjust=True) ──► quant_filter + debate  │
│  Stockbit API (fundamentals, sentiment, orderbook) ──► debate only  │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ top-10 candidates JSON
┌──────────────────────────────▼──────────────────────────────────────┐
│                COMPONENT 1: STOCK SCREENING                         │
│  core/quant_filter/pipeline.py  (1118 lines)                        │
│  core/quant_filter/config.py    (447 lines)                         │
│                                                                     │
│  Static filter: Close>100, DER≤sector_max, PBV_pctile<80%, PBV<6   │
│  Volume gate: 5d zero-vol / (recent/avg_20d < 0.10)                 │
│  Composite score (100 pts):                                         │
│    30% Fundamental (Val 20 + Prof 10)                               │
│    70% Technical (RSI 25 + Vol 25 + PriceMom 20)                    │
│  Sorted by score → top 10 saved to top10_candidates.json            │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ + regime signal
┌──────────────────────────────▼──────────────────────────────────────┐
│             COMPONENT 2: SIGNAL GENERATION (trade envelope)         │
│  core/regime.py — 20d realized vol of ^JKSE                         │
│  services/debate_chamber.py — _compute_trade_envelope()             │
│    entry_zone = near MA50 (−3% to MA50x1.02)                        │
│    stop = max(SMA20−ATR, price−2xATR); floor = price×0.92           │
│    target = resistance OR FV ceiling; cap = entry×1.15              │
│    ALL prices snapped to IHSG tick sizes (Python, deterministic)    │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ envelope injected verbatim into CIO
┌──────────────────────────────▼──────────────────────────────────────┐
│              COMPONENT 3: BULL vs BEAR DEBATE                       │
│  services/debate_chamber.py — LangGraph state machine               │
│                                                                     │
│  Phase 1 (PARALLEL, scout):                                         │
│    fundamental_node (flash) ─┐                                      │
│    chartist_node (PRO)  ─────┼──► synthesizer_node ──► RAG bundle  │
│    sentiment_node (flash)  ──┘                                      │
│                                                                     │
│  Phase 2 (serial, debate):                                          │
│    bull_analyst (flash, R1/R2) ──► bear_auditor (flash, R1/R2)      │
│    ──► consensus_evaluator ──[3 rounds max or 60% vote]──►          │
│    devils_advocate (flash) ──► cio_judge (PRO)                      │
│                                                                     │
│  Post-judge overrides (Python, deterministic):                      │
│    _apply_envelope() → _apply_consensus_override() →                │
│    _apply_news_adjustment() → _apply_staleness_adjustment()         │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ CIOVerdict JSON
┌──────────────────────────────▼──────────────────────────────────────┐
│              POST-DEBATE CHAIN                                      │
│  core/risk_governor.py — deterministic gate                         │
│    HARD REJECT: rating_not_buyable, overvalued, rr_too_low,         │
│    rr_implausible(>=5.0), insufficient_technical_data               │
│    SOFT: counter_trend, apply_defensive_guard (downgrade sizing)    │
│  core/historical_scorer.py — realized EV/win-rate adjustment        │
│  core/quant_filter/position_sizer.py — Kelly + lot-size + cap       │
│  generate_top3_report ──► output/TOP_3_SWING_TRADES.md             │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ also writes to
                               ▼
         output/backtest/backtest_memory.jsonl (auto-record ALL ratings)
         core/backtest_outcome_evaluator.py (15d horizon, yfinance)
```

**Observed pipeline run (2026-06-11):**
- Screener produced 10 candidates: DMAS(67.5), MPMX(59.5), PSAB(57.5), MAPI(56.5), BMRI(44.5), INDO(40.5), ACES(39.5), HATM(30.5), TLKM(24.5), RAJA(19.0)
- Full pipeline run: **1 stock debated, 0 BUY eligible, 0 selected** → empty TOP_3 report (root cause: quant filter may have yielded only 1 debate candidate; cause unconfirmed — see I4-01)
- Standalone debate run (2026-06-11 16:30): DMAS HOLD 2.00x, MPMX HOLD 2.00x, PSAB AVOID 2.67x, MAPI HOLD 2.00x, BMRI HOLD 2.21x — all R/R clustered at floor, zero BUY in current market
- Backtest memory: 80 closed records: 1W / 79L; 78 loss-cause = stop_hit; avg 1.6 days to stop

---

## COMPONENT 1 — Stock Screening & Filtering

### Strengths
| # | Strength | Evidence |
|---|---|---|
| S1-1 | Multi-stage gate: static price/DER/PBV → volume suspicion → ADT floor | `pipeline.py:297-303, 431-433` |
| S1-2 | Composite score with sector-relative valuation (PBV percentile vs benchmark) | `config.py:122-149` |
| S1-3 | Sector-aware DER ceiling per 13 sectors | `config.py:62-76` |
| S1-4 | Piotroski F-Score (>=4) and Modified Altman Z-Score (>=1.1) integrated | `config.py:37-55` |
| S1-5 | Relative strength vs IHSG (1-month) included in momentum scoring | `pipeline.py:530-545` |
| S1-6 | TICKER_SECTOR_HARDCODE for 70+ tickers avoids keyword misclassification | `config.py:200-350` |
| S1-7 | Graham Number with IHSG-calibrated k=18.2; bull/bear variants with cap 5x | `pipeline.py:916-944` |

### Issues

---

#### C1-F01 CRITICAL — Fundamental failures demoted from hard reject to penalty-only

**File:** `core/quant_filter/config.py:156-165`  
**Impact:** Stocks with negative ROE, severe revenue decline (-75%), or Piotroski F-Score < 4 can now pass screening if technical momentum is strong enough. NZIA (-75% revenue, negative ROE) appeared in backtest records and produced the one "win" — a pre-fix era anomaly.

**BEFORE (actual `config.py` lines 156-161):**
```python
# ── Turnaround Momentum Penalties (v3.2)
# Fundamental yang buruk tidak lagi langsung dibuang, tapi diberi penalti berat.
"penalty_roe_fail": -15,       # was formerly a hard reject in v3.1
"penalty_piotroski_fail": -15, # was formerly min_piotroski=4 hard gate
"penalty_altman_z_fail": -20,  # was formerly min_altman_z=1.1 hard gate
# No revenue-decline penalty key exists — revenue filter absent entirely
```

**AFTER (option A — restore hard reject for the worst cases):**
```python
# core/quant_filter/pipeline.py ~line 900, inside static_filter():
HARD_FUNDAMENTAL_FAILS = [
    ("ROE < -0.05", lambda r: r.get("ROE", 0) < -0.05),
    ("Revenue YoY < -50%", lambda r: r.get("rev_yoy_pct", 0) < -50),
]
for reason, check in HARD_FUNDAMENTAL_FAILS:
    if check(row):
        reasons.append(reason)
        return None  # hard reject before scoring
```

**AFTER (option B — raise penalties to disqualify at composite level):**
```python
"penalty_roe_fail": -30,       # ensures composite < 0 → rank-bottom
"penalty_piotroski_fail": -30,
"penalty_altman_z_fail": -40,
```

---

#### C1-F02 CRITICAL — No data freshness gate on screener XLSX input

**File:** `core/quant_filter/config.py:10-30` (`_find_latest_xlsx`)  
**Impact:** Today's run (2026-06-11) used XLSX dated 2026-06-04 — 7 days stale. No warning was emitted. The pipeline issued BUY recommendations on outdated fundamental data with no indication to the user.

**BEFORE (actual `config.py` lines 10-30):**
```python
def _find_latest_xlsx(output_dir: str = "output") -> str:
    patterns = [
        os.path.join(output_dir, "IDX_Fundamental_Analysis_*.xlsx"),
        os.path.join(output_dir, "IDX Fundamental Analysis *.xlsx"),
    ]
    found = []
    for pat in patterns:
        found.extend(glob.glob(pat))
    if not found:
        raise FileNotFoundError(...)
    return str(Path(sorted(found, reverse=True)[0]))  # no age check
```

**AFTER:**
```python
from datetime import datetime

MAX_XLSX_AGE_TRADING_DAYS = 3   # also add to CONFIG dict

def _find_latest_xlsx(max_age_days: int = MAX_XLSX_AGE_TRADING_DAYS) -> Path:
    xlsx_files = sorted(BASE_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not xlsx_files:
        raise FileNotFoundError("No .xlsx files found")
    latest = xlsx_files[-1]
    age_days = (datetime.now() - datetime.fromtimestamp(latest.stat().st_mtime)).days
    if age_days > max_age_days:
        import warnings
        warnings.warn(
            f"[Screener] XLSX is {age_days} days old (>{max_age_days}d threshold). "
            f"Fundamental data may be stale. File: {latest.name}",
            stacklevel=2,
        )
    return latest
```

---

#### C1-F03 HIGH — ADT liquidity floor too low for swing trades (Rp 5B)

**File:** `core/quant_filter/config.py:87`  
**Impact:** Rp 5B ADT means approximately 2,500 lots/day at Rp 2,000/share. A meaningful swing position cannot be entered or exited cleanly. NZIA, KUAS, MPOW appear in backtest loss records — all micro-caps that pass this floor.

**BEFORE:**
```python
"min_adt_20d": 5_000_000_000,   # Rp 5 billion
```

**AFTER:**
```python
"min_adt_20d": 20_000_000_000,  # Rp 20 billion minimum for swing execution
```

---

#### C1-F04 HIGH — EPS back-calculated from P/E x Price is circular

**File:** `services/fair_value_calculator.py:1017-1025`  
**Impact:** When EPS is missing, the code derives `eps = current_price / pe_ratio`. This EPS feeds Graham Fair Value: `sqrt(k * eps * bvps)`. The resulting FV is anchored to the market price being valued — violating the independence assumption of intrinsic valuation. This derived EPS currently counts as a "valid method" in the quality gate at line 1050.

**BEFORE (actual `fair_value_calculator.py` lines 1017-1025):**
```python
if stats.eps_ttm == 0.0 and stats.raw_pe_current > 0 and current_price > 0:
    stats.eps_ttm = round(current_price / stats.raw_pe_current, 2)
    logger.info(
        "[FairValue] {}: EPS back-calculated from PE ({} / {} = {})",
        ticker, current_price, stats.raw_pe_current, stats.eps_ttm,
    )
# Quality gate (lines 1050-1079) only checks confidence=="LOW" and net_margin>1.0;
# derived EPS is not flagged as a separate quality signal.
```

**AFTER:**
```python
eps_derived = False
if eps is None and pe is not None and current_price > 0:
    eps = current_price / pe
    eps_derived = True
    logger.info(f"[FairValue] EPS back-calculated from PE (derived, circular): {eps:.4f}")

# In quality gate (lines 1050-1079):
if eps_derived:
    valid_method_count -= 1   # Graham with derived EPS does not count as independent method
    result["eps_source"] = "derived_from_pe"
```

---

#### C1-F05 MEDIUM — RSI hard reject threshold at 80 instead of standard 70

**File:** `core/quant_filter/config.py:98`  
**Impact:** RSI 70-79 is the established overbought zone. Stocks in this range are already extended and risky for swing entries. Allowing them through until RSI=80 passes clearly overbought stocks to the expensive debate pipeline.

**BEFORE:**
```python
"rsi_hard_reject": 80,
```

**AFTER:**
```python
"rsi_hard_reject": 70,
# For mean-reversion screener mode, expose separately: rsi_hard_reject_mean_rev = 85
```

---

#### C1-F06 MEDIUM — No ATR% filter; hyper-volatile micro-caps pass on momentum score

**File:** `core/quant_filter/pipeline.py:431-450` (ADT gate section)  
**Impact:** A stock with 6% daily ATR invalidates a 4% stop-loss on any normal day. The screener has no volatility ceiling.

**AFTER — add after ADT gate:**
```python
MAX_ATR_PCT = 0.04   # add to CONFIG
atr_pct = atr_val / close_price if close_price > 0 else 0
if atr_pct > MAX_ATR_PCT:
    reasons.append(f"ATR% {atr_pct:.1%} > max {MAX_ATR_PCT:.0%}")
    return None
```

---

#### C1-F07 MEDIUM — All 10 candidates today had sub-average volume (8 of 10 below 1.0x)

**Observed from `scratch/report.md` (2026-06-11 run):** Volume surge ratios 0.16x-1.34x. A candidate with 0.16x recent volume cannot execute a swing entry without moving the price significantly against itself.

**File:** `core/quant_filter/pipeline.py:476-495`

**AFTER — add to static filter:**
```python
MIN_VOLUME_SURGE_FOR_CANDIDATE = 0.30   # add to CONFIG
recent_vs_avg = (volume.tail(5).mean() / volume.tail(20).mean()
                 if volume.tail(20).mean() > 0 else 0)
if recent_vs_avg < MIN_VOLUME_SURGE_FOR_CANDIDATE:
    reasons.append(f"Volume anemia: {recent_vs_avg:.2f}x avg-20d")
    return None
```

---

## COMPONENT 2 — Swing Trade Signal Generation

### Strengths
| # | Strength | Evidence |
|---|---|---|
| S2-1 | All entry/target/stop computed in Python (deterministic), LLM cannot override | `debate_chamber.py:3191-3314` |
| S2-2 | IHSG tick-size snapping applied to all prices | `utils/technicals.py:38-47` |
| S2-3 | Entry zone anchored to MA50 (pullback principle, not chase) | `debate_chamber.py:3202-3208` |
| S2-4 | Dual ceiling: FV ceiling AND swing cap prevent inflated R/R | `debate_chamber.py:3273-3285` |
| S2-5 | ATR(14) proxy fallback when series too short | `debate_chamber.py:2145-2147` |
| S2-6 | MA200 context computed with crossover detection (recent 5-day) | `debate_chamber.py:2172-2191` |
| S2-7 | R/R implausibility ceiling (>=5.0 hard reject) prevents fantasy setups | `core/risk_governor.py` |

### Issues

---

#### C2-F01 CRITICAL — Stop-loss ~4% is smaller than daily noise; 78/79 stops hit in avg 1.6 days

**File:** `services/debate_chamber.py:3222-3237`  
**Evidence:** `backtest_memory.jsonl` — 78/79 losses caused by stop_hit; avg holding period 1.6 days; 75/79 within 3 days. A 3-month swing trade stopped out in under 2 days is stop-noise. The formula produces stops ~4% below entry for typical IHSG mid-caps (ADRO 2230 to 2140 = 4.0%, WIIM 1575 to 1510 = 4.1%) — less than their daily high-low range.

**BEFORE:**
```python
# debate_chamber.py lines 3222-3237
if atr14 > 0 and sma20 > 0:
    stop_candidate_1 = sma20 - atr14              # 1x ATR below SMA20
    stop_candidate_2 = current_price - (2.0 * atr14)   # fixed 2x ATR
    stop = max(stop_candidate_1, stop_candidate_2)
    hard_floor = current_price * 0.92
    stop = snap_to_tick(max(stop, hard_floor))
```

**AFTER — regime-scaled multiplier + noise rejection:**
```python
# debate_chamber.py lines 3222-3237
REGIME_ATR_MULTIPLIER = {
    "BULLISH":   2.0,
    "NEUTRAL":   2.5,
    "DEFENSIVE": 3.0,
}
regime = str((state.get("metadata") or {}).get("regime", "NEUTRAL")).upper()
k_atr = REGIME_ATR_MULTIPLIER.get(regime, 2.5)

if atr14 > 0 and sma20 > 0:
    stop_candidate_1 = sma20 - atr14
    stop_candidate_2 = current_price - (k_atr * atr14)
    stop = max(stop_candidate_1, stop_candidate_2)
    hard_floor = current_price * 0.92
    stop = snap_to_tick(max(stop, hard_floor))

# After computing entry_high, add noise rejection gate:
stop_distance = entry_high - stop
noise_floor = 1.5 * atr14
if stop_distance < noise_floor:
    # Return sentinel; _cio_judge_node must handle and fall back to HOLD
    return {
        "rejected": True,
        "reason": f"stop_inside_noise: gap {stop_distance:.0f} < 1.5xATR {noise_floor:.0f}",
    }
```

---

#### C2-F02 CRITICAL — DEFENSIVE regime guard downgrades sizing metadata but does not block BUY production in output

**File:** `core/risk_governor.py:266-287` (`apply_defensive_guard`), `core/orchestrator/legacy.py:3602, 5668`  
**Evidence:** 80 trades recorded during DEFENSIVE regime (IHSG -7.2% in 5 days). `apply_defensive_guard` IS wired and IS called inside `evaluate_risk()` — it correctly sets `status="watchlist_only", sizing_allowed=False` when regime is DEFENSIVE. The gap is architectural: `annotate_risk()` (line 3602) stores this as metadata in `result["risk_governor"]`, but top-3 candidate selection reads `result["verdict"]["rating"]`, not `risk_governor.sizing_allowed`. A BUY verdict with `sizing_allowed=False` is still eligible for TOP_3_SWING_TRADES.md.

**BEFORE (actual code — `apply_defensive_guard` works correctly in isolation):**
```python
# risk_governor.py lines 266-287
def apply_defensive_guard(decision: RiskDecision, candidate: dict) -> RiskDecision:
    if decision.status != "deployable" or not decision.sizing_allowed:
        return decision
    if _market_regime(candidate) != "DEFENSIVE":
        return decision
    return decision.model_copy(update={
        "status": "watchlist_only",
        "sizing_allowed": False,
        "reason_codes": _dedupe([*decision.reason_codes, "market_regime_defensive"]),
    })

# legacy.py — top-3 selection does NOT filter on sizing_allowed:
# Line 3602: annotate_risk() writes to result["risk_governor"] (metadata only)
# Line 5668: _annotate_risk_governor(top_n) — applied AFTER selection, too late to exclude
```

**AFTER — add hard clamp in `_apply_consensus_override` (debate_chamber.py:3470):**
```python
def _apply_consensus_override(self, parsed: dict, state: DebateChamberState) -> dict:
    p = dict(parsed) if isinstance(parsed, dict) else {}

    # DEFENSIVE regime: hard clamp before any other override
    regime = str(
        (state.get("metadata") or {}).get("regime", "") or
        (state.get("technical_indicators") or {}).get("regime", "")
    ).upper()
    if regime == "DEFENSIVE" and str(p.get("rating", "")).upper() in ("BUY", "STRONG_BUY"):
        p["rating"] = "HOLD"
        p["confidence"] = min(float(p.get("confidence") or 0.0), 0.55)
        p["weighted_reasoning"] = self._append_reason(
            p.get("weighted_reasoning"),
            "DEFENSIVE regime: BUY clamped to HOLD — no new long entries during market correction.",
        )

    # ... existing override logic unchanged below ...
```

---

#### C2-F03 HIGH — MAX_TARGET_RETURN=0.15 contradicts CIO prompt header "3-10% target"

**File:** `services/debate_chamber.py:3189` vs `services/debate_prompts/cio_judge.txt:1`  
**Impact:** CIO prompt anchors the LLM to expect 3-10% targets. Python envelope can produce 14% targets. The LLM's reasoning language ("modest 7% return") doesn't match what was actually computed, producing incoherent CIO output.

**BEFORE:**
```python
MAX_TARGET_RETURN = 0.15   # 15%
```

**AFTER:**
```python
MAX_TARGET_RETURN = 0.10   # 10% — aligned with CIO prompt guidance
```
Also update `cio_judge.txt` header line to reflect actual ceiling.

---

#### C2-F04 MEDIUM — ATR multiplier fixed at 2.0x regardless of regime (addressed in C2-F01)

**File:** `services/debate_chamber.py:3225`  
This is the underlying cause of C2-F01. The fix in C2-F01 resolves it. Listed separately to track as an independent calibration issue.

---

## COMPONENT 3 — Bull vs Bear Debate Mechanism

### Strengths
| # | Strength | Evidence |
|---|---|---|
| S3-1 | LangGraph state machine with pure async nodes; no hidden mutation between nodes | `debate_chamber.py:4145-4183` |
| S3-2 | Bull/Bear prompts separated by round (R1 open position, R2 rebuttal) | `debate_chamber.py:2810-2851` |
| S3-3 | Bear always receives Bull's latest argument — prevents straw-man | `debate_chamber.py:2854-2857` |
| S3-4 | State cleaner deterministic (no LLM compression) — regex-preserves all price mentions | `debate_chamber.py:2940-3001` |
| S3-5 | CIO price redaction from debate history — CIO prices from Python envelope only | `debate_chamber.py:3781` |
| S3-6 | Three-tier consensus: voting 80%/60% threshold, soft_hold, confidence_winner fallback | `debate_chamber.py:2896-2932` |
| S3-7 | Devil's advocate injected when consensus reached early — prevents rubber-stamp | `debate_chamber.py:4176-4180` |
| S3-8 | Margin-of-safety alert prepended to raw_data in synthesizer_node | `debate_chamber.py:2548-2558` |
| S3-9 | Adaptive planner: RETRY/PROCEED_PARTIAL/SKIP_TICKER/ABORT_BATCH | `core/adaptive_planner.py` |
| S3-10 | Global rules injected per LLM call: date awareness, null vs zero, consistency | `debate_chamber.py:1-1917` |

### Issues

---

#### C3-F01 CRITICAL — cio_judge.txt contains "R/R >= 5.0 justifies BUY" doctrine

**File:** `services/debate_prompts/cio_judge.txt:41-47, 64-69`  
**Impact:** The risk governor hard-rejects R/R >= 5.0 (`RR_IMPLAUSIBLE_CEILING = 5.0`). The CIO judge prompt still instructs the LLM that R/R >= 5 is sufficient justification for a BUY recommendation. The LLM produces reasoning that will always be downstream-rejected, wastes a pro-LLM call, and generates incoherent output (seen in audit_log.jsonl: INDO R/R 22.3x, reasoning "extreme asymmetry sufficient to validate"). This was flagged in rr-sanity but not cleaned.

**BEFORE (verified from cio_judge.txt lines 43-44, 65-68):**
```
STEP 1 line 43: "R/R ≥ 5.0 → Proceed to STEP 3 conflict resolution. Extreme asymmetry
                 may justify entry despite overvaluation"
STEP 3 line 65: "Fundamental ❌ + Technical ❌ →
                   IF R/R ≥ 5.0 AND Sentiment ≠ BEARISH: HOLD (Extreme Asymmetry Watchlist)"
```
Note: STEP 3 yields HOLD, not BUY, for the R/R ≥ 5.0 case. The contradiction is
that `risk_governor.py` hard-rejects R/R ≥ 5.0 as implausible data, while the CIO
prompt reasons about it as a valid "watchlist" signal — the LLM spends a pro-call
producing reasoning that will be overridden before output.

**AFTER — replace with:**
```
R/R 1.3-2.5: Standard swing setup. Weight all three signals equally.
R/R 2.5-4.9: Strong setup. Require confirmation from at least 2 of 3 signals.
R/R >= 5.0:  IMPLAUSIBLE. The envelope has been capped by Python — this value
             should not appear. If you see it, flag in weighted_reasoning and rate HOLD.
```

Bump `prompt_version` in `manifest.json`. Add entry in `PROMPT_MIGRATION.md`. Run `pytest tests/test_debate_chamber_reliability.py -v`.

---

#### C3-F02 HIGH — Bull and Bear R1 prompts are 14-15 lines with no required data citation

**Files:** `services/debate_prompts/bull_r1.txt` (15 lines), `bear_r1.txt` (14 lines) vs `cio_judge.txt` (172 lines)  
**Impact:** Without minimum data citation requirements, analysts can make the same qualitative argument across all three rounds. Multi-round debate does not converge because R2/R3 arguments add no new evidence. The CIO receives no material information beyond what was in Round 1.

**BEFORE (bull_r1.txt — paraphrased):**
```
You are the Bull analyst. Follow 4 rules. Declare BUY/HOLD/AVOID with confidence.
```

**AFTER:**
```
You are the Bull Analyst (ROUND 1 — Opening Position).

REQUIRED: Your argument MUST cite at least 3 specific data points from the brief:
1. One fundamental metric with its actual number (e.g., "ROE 18.4% vs sector avg 12%")
2. One technical metric with its actual number (e.g., "RSI 52, price 1.8% below MA50")
3. One catalyst or risk factor specific to this company (not generic market commentary)

PROHIBITED: "Strong fundamentals" without the supporting figure.
PROHIBITED: Repeating the brief without analysis or synthesis.

If you cannot cite 3 specifics due to data gaps, declare HOLD with confidence <= 0.50
and explicitly state what data is missing.

Final mandatory line:
POSITION: [BUY|HOLD|AVOID] CONFIDENCE: [0.00-1.00]
```

Mirror the same citation requirement in `bear_r1.txt`.

---

#### C3-F03 HIGH — Agent calibration weights always default to 1.0; never fitted from data

**File:** `services/debate_chamber.py` (`DEFAULT_AGENT_CALIBRATION_WEIGHTS`)  
**Evidence:** Every run logs "Agent calibration weights not configured — defaulting to 1.0". The `observations.jsonl` and backtest outcome data exist for fitting. Bull at 100% confidence and Bear at 0% confidence (seen in NZIA) are treated as equally credible.

**AFTER — offline calibration script:**
```python
# scripts/fit_agent_weights.py
import json
from pathlib import Path

def fit_weights(
    obs_path: str = "output/observations.jsonl",
    backtest_path: str = "output/backtest/backtest_memory.jsonl",
) -> dict[str, float]:
    """
    Compute Brier-score-derived weights per agent.
    Minimum 30 BUY-only samples required before trusting the weight.
    """
    observations = [
        json.loads(l) for l in Path(obs_path).read_text().splitlines() if l.strip()
    ]
    outcomes = {
        r["ticker"]: r
        for r in [json.loads(l) for l in Path(backtest_path).read_text().splitlines() if l.strip()]
        if r.get("outcome") and r.get("rating") in ("BUY", "STRONG_BUY")
    }

    agent_errors: dict[str, list[float]] = {}
    for obs in observations:
        ticker, agent = obs.get("ticker"), obs.get("agent")
        confidence = float(obs.get("confidence") or 0.0)
        if ticker not in outcomes or not agent:
            continue
        win = float(outcomes[ticker].get("outcome") == "win")
        agent_errors.setdefault(agent, []).append((confidence - win) ** 2)

    MIN_SAMPLE = 30
    weights: dict[str, float] = {}
    for agent, errors in agent_errors.items():
        if len(errors) < MIN_SAMPLE:
            weights[agent] = 1.0   # insufficient data, stay neutral
            continue
        avg_brier = sum(errors) / len(errors)
        # Lower Brier error -> higher weight; scale 0.5-1.5
        weights[agent] = round(max(0.5, min(1.5, 1.0 - (avg_brier - 0.25) * 2)), 3)

    out = Path("core/agent_calibration_weights.json")
    out.write_text(json.dumps(weights, indent=2))
    print(f"Wrote weights: {weights}")
    return weights
```

Load in `DebateChamber.__init__()` if the file exists.

---

#### C3-F04 HIGH — ExDate arithmetic delegated to LLM; Python pre-computation available but unused

**File:** `services/debate_prompts/cio_judge.txt:9-34`  
**Impact:** LLMs make date arithmetic mistakes under context pressure. The `utils/exdate_scanner.py` module already runs in `_synthesizer_node` but injects raw ex-date info rather than the policy decision. The CIO is instructed to "calculate the days" — it should only read the result.

**BEFORE (cio_judge.txt STEP 0):**
```
Calculate days since ExDate:
  - ExDate <= 7 days -> AVOID
  - ExDate 8-14 days -> cap confidence at 0.65
```

**AFTER — inject gate result in synthesizer_node:**
```python
# debate_chamber.py _synthesizer_node(), after exdate_info is computed:
from datetime import date

def _compute_exdate_gate(exdate_info, today: date) -> str:
    if exdate_info is None or getattr(exdate_info, "status", "CLEAR") == "CLEAR":
        return "EXDATE_GATE: CLEAR"
    days_to_ex = (exdate_info.ex_date - today).days
    if days_to_ex <= 7:
        return f"EXDATE_GATE: AVOID (ExDate in {days_to_ex}d — do not enter)"
    if days_to_ex <= 14:
        return f"EXDATE_GATE: CAP_65 (ExDate in {days_to_ex}d — cap confidence at 0.65)"
    return f"EXDATE_GATE: MONITOR (ExDate in {days_to_ex}d — no constraint)"

raw = f"{_compute_exdate_gate(exdate_info, date.today())}\n\n" + raw
```

Update `cio_judge.txt` STEP 0: "Read the EXDATE_GATE line at the top of the brief and follow it — do not recalculate."

---

#### C3-F05 MEDIUM — Chartist scout uses pro_llm instead of flash_llm

**File:** `services/debate_chamber.py:2300`  
**Impact:** Chartist receives Python-computed ground-truth technicals (RSI, MA50, ATR, volume) and raw orderbook JSON. It interprets pre-computed numbers, not performing novel financial analysis. Pro_llm costs ~4x more per call. CLAUDE.md states scouts use flash. This single line accounts for ~25% of per-ticker cost inflation.

**BEFORE:**
```python
# debate_chamber.py line 2300
resp = await self._invoke_llm_for_state(state, self.pro_llm, messages)
```

**AFTER:**
```python
resp = await self._invoke_llm_for_state(state, self.flash_llm, messages)
# If pro was intentional: # intentional: orderbook spread analysis benefits from deeper reasoning
```

---

#### C3-F06 MEDIUM — HOLD verdicts recorded as trades in backtest memory (73 of 80 records)

**File:** `core/orchestrator/legacy.py` (backtest recording section)  
**Evidence:** 73 of 80 closed backtest records are HOLD verdicts recorded as if executed. The EV/win-rate scorer in `core/historical_scorer.py` penalizes tickers for trades the system never intended to enter.

**BEFORE:**
```python
if verdict.rating not in ("AVOID", "INSUFFICIENT_DATA"):
    _record_backtest(verdict, ...)   # HOLD is recorded as an executed trade
```

**AFTER:**
```python
WATCHLIST_LOG_PATH = BASE_DIR / "output/backtest/watchlist_log.jsonl"

if verdict.rating in ("BUY", "STRONG_BUY"):
    _record_backtest(verdict, backtest_type="trade")
elif verdict.rating == "HOLD":
    _record_backtest(verdict, path=WATCHLIST_LOG_PATH, backtest_type="watchlist")
# AVOID / INSUFFICIENT_DATA: not recorded
```

---

#### C3-F07 MEDIUM — Devil's advocate shows "--" in output table; adversarial vote not extracted

**File:** `services/debate_chamber.py:3003-3033`  
**Impact:** The devil's advocate injects a challenge but does not extract a vote. Output table renders "--" as if the agent abstained. Readers interpret the 5th agent as neutral rather than adversarial.

**AFTER — force adversarial position at node exit:**
```python
# debate_chamber.py _devils_advocate_node(), final lines:
content, signal = self._ensure_signal_footer(resp.content, "devils_advocate")
if signal.get("position") not in ("AVOID", "HOLD"):
    content += "\n\nPOSITION: AVOID CONFIDENCE: 0.40"
    signal = {"position": "AVOID", "confidence": 0.40}
```

---

## PHASE 4 — Cross-Component Integration Audit

### I4-01 HIGH — Full pipeline only debated 1 of 10 candidates; root cause unconfirmed

**Evidence:** Full pipeline run (14:48:58) output: "Stocks Debated: 1 | Eligible: 0 | Selected: 0." Standalone `uv run idx debate DMAS MPMX PSAB MAPI BMRI` ran all 5 successfully (22m 55s, no budget error). Confirmed candidates: budget exhaustion after first full-pipeline debate, OR quant filter produced only 1 ticker for the debate stage, OR the batch file held a different candidate list than the screener output. Root cause requires log/ledger inspection for run_id `20260611_144852`. The title "TOP 0 HIGH-CONVICTION TRADES" is misleading regardless of cause.

**File:** `core/budget.py` + `core/orchestrator/legacy.py`

**AFTER:**
```python
# orchestrator — before starting debate batch:
TOKENS_PER_TICKER_ESTIMATE = 50_000   # calibrate from telemetry
available_budget = budget_tracker.remaining()
max_tickers_feasible = available_budget // TOKENS_PER_TICKER_ESTIMATE
if max_tickers_feasible < len(candidates):
    logger.warning(
        f"[Orchestrator] Budget {available_budget} tokens supports "
        f"{max_tickers_feasible}/{len(candidates)} tickers."
    )
    candidates = candidates[:max_tickers_feasible]

# In report header: "Candidates Evaluated: {debated}/{total} ({skipped} skipped: budget)"
```

### I4-02 HIGH — No portfolio-level risk management; positions sized independently

**Files:** `core/quant_filter/position_sizer.py:287`  
**Impact:** During the observed crash period, the system could recommend 10 concurrent BUY positions, all in the same declining market, with no aggregate open-risk ceiling and no drawdown kill-switch.

**AFTER:**
```python
# core/risk_governor.py — add to evaluate_risk():
MAX_PORTFOLIO_OPEN_RISK_PCT = 0.06  # 6% total open risk at any time

def _portfolio_heat_ok(new_risk_pct: float, open_positions: list) -> bool:
    current_heat = sum(p.get("risk_pct", 0.0) for p in open_positions)
    return (current_heat + new_risk_pct) <= MAX_PORTFOLIO_OPEN_RISK_PCT
```

### I4-03 HIGH — No deduplication in backtest memory; same ticker recorded 2.6x on average

**Evidence:** 79 loss records from 30 unique tickers = 2.6 records/ticker. The historical scorer penalizes the same ticker repeatedly for the same position, biasing it permanently downward.

**AFTER:**
```python
# In _record_backtest():
def _record_backtest(verdict, ...):
    key = (verdict.ticker, verdict.entry_price_low, verdict.target_price, verdict.stop_loss)
    if _find_existing_record(key):
        _update_record_timestamp(key)   # update run date, don't duplicate
        return
    _append_new_record(verdict, ...)
```

### I4-04 MEDIUM — Backtest evaluation horizon 15 days vs current 5-20 trading-day trade thesis

**File:** `core/backtest_outcome_evaluator.py:20`

**BEFORE:**
```python
EVALUATION_HORIZON_TRADING_DAYS = 15
```

**AFTER:**
```python
EVALUATION_HORIZON_TRADING_DAYS = 20   # max normal 5-20 trading-day swing evaluation
# Also add: label "timeout_flat" when position closes at day 20 within +/-2% entry
# (not stop_hit, not target_hit) — distinguished from actual loss
```

---

## PHASE 5 — Findings Dashboard

| ID | Component | Severity | Finding | Status |
|---|---|---|---|---|
| F01 | Screening | CRITICAL | Fundamental failures penalty-only; negative-ROE stocks pass on momentum | Open → C1-F01 |
| F02 | Screening | CRITICAL | No XLSX freshness gate; today's run used 7-day-old fundamentals silently | Open → C1-F02 |
| F03 | Screening | HIGH | ADT floor Rp 5B too low for meaningful swing execution | Open → C1-F03 |
| F04 | Screening | HIGH | EPS back-calc from P/E circular; FV anchored to market price | Open → C1-F04 |
| F05 | Screening | MEDIUM | RSI hard reject at 80; overbought stocks pass at RSI 70-79 | Open → C1-F05 |
| F06 | Screening | MEDIUM | No ATR% ceiling; hyper-volatile stocks pass if momentum high | Open → C1-F06 |
| F07 | Screening | MEDIUM | 8/10 candidates today had sub-average volume; no minimum confirmation | Open → C1-F07 |
| F08 | Signal Gen | CRITICAL | Stop ~4% < daily noise; 78/79 stops hit in avg 1.6 days (backtest evidence) | Open → C2-F01 |
| F09 | Signal Gen | CRITICAL | DEFENSIVE regime doesn't block BUY production; only downgrades sizing | Open → C2-F02 |
| F10 | Signal Gen | HIGH | MAX_TARGET_RETURN 15% contradicts CIO prompt "3-10%" | Open → C2-F03 |
| F11 | Signal Gen | MEDIUM | ATR multiplier fixed at 2.0x regardless of regime (addressed in C2-F01) | Open → C2-F04 |
| F12 | Signal Gen | MEDIUM | Entry zone uses price discount only; no momentum confirmation required | Open |
| F13 | Debate | CRITICAL | cio_judge.txt still contains "R/R >= 5 justifies BUY" doctrine (contradicts hard-reject) | Open → C3-F01 |
| F14 | Debate | HIGH | Bull/Bear R1 prompts 14-15 lines; no required data citation | Open → C3-F02 |
| F15 | Debate | HIGH | Agent calibration defaults to 1.0 every run; never fitted from data | Open → C3-F03 |
| F16 | Debate | HIGH | ExDate arithmetic delegated to LLM; Python result not pre-computed | Open → C3-F04 |
| F17 | Debate | MEDIUM | Chartist uses pro_llm (4x cost) for deterministic tech interpretation | Open → C3-F05 |
| F18 | Debate | MEDIUM | HOLD recorded as trade; 73/80 backtest records are HOLD-as-trade | Open → C3-F06 |
| F19 | Debate | MEDIUM | Devil's advocate shows "--"; adversarial vote not extracted | Open → C3-F07 |
| F20 | Integration | HIGH | Full pipeline debated 1/10 candidates; root cause unconfirmed (budget/filter/batch) | Open → I4-01 |
| F21 | Integration | HIGH | No portfolio heat cap or drawdown kill-switch | Open → I4-02 |
| F22 | Integration | HIGH | No dedup in backtest memory; same ticker recorded 2.6x on average | Open → I4-03 |
| F23 | Integration | MEDIUM | Backtest horizon 15d vs stated 5-20 trading-day thesis | Open → I4-04 |
| F24 | Realized | SYSTEMIC | 80 trades closed: 1W/79L, avg PnL -3.29%, avg 1.6 days to stop | Systemic |
| FXX | All | FIXED | Target inflation R/R 22x, FV garbage anchor, voting bypass, tent R/R | FIXED (rr-sanity-v1..v3) |

**Count:** 5 CRITICAL open, 9 HIGH open, 9 MEDIUM open, 1 systemic

---

## PHASE 6 — Prioritized Improvement Roadmap

### P0 — This Week (before next production run)

| # | Task | Key Files | Success Test |
|---|---|---|---|
| P0.1 | DEFENSIVE regime hard-clamps BUY to HOLD in `_apply_consensus_override` | `debate_chamber.py:3470` | Run DMAS in DEFENSIVE regime -> 0 BUY verdicts |
| P0.2 | Regime-scaled ATR multiplier + noise rejection gate | `debate_chamber.py:3222-3237` | ATR=100, entry=1000, DEFENSIVE -> stop >= 700 |
| P0.3 | Remove R/R >= 5 doctrine from cio_judge.txt; bump prompt_version | `cio_judge.txt:41-47,64-69`, `manifest.json` | `pytest tests/test_debate_chamber_reliability.py -v` |
| P0.4 | HOLD -> watchlist_log; only BUY/STRONG_BUY as executed trades | `legacy.py` backtest section | 73 HOLD records migrated; scorer sees BUY-only |
| P0.5 | XLSX freshness gate with warning | `config.py:_find_latest_xlsx()` | 4-day-old XLSX -> warning logged; run continues |
| P0.6 | Budget reservation before batch; report "Skipped N candidates" | `legacy.py`, `core/budget.py` | 10 candidates, 40k budget -> 4 debated, 6 reported skipped |

### P1 — Within 2 Weeks

| # | Task | Files |
|---|---|---|
| P1.1 | ADT floor to Rp 20B; add ATR% <= 4% gate | `config.py:87`, `pipeline.py:431-450` |
| P1.2 | EPS derived flag; exclude from valid method count in quality gate | `fair_value_calculator.py:1017-1025, 1050-1079` |
| P1.3 | ExDate gate pre-computed in Python; LLM reads result | `debate_chamber.py:_synthesizer_node`, `cio_judge.txt:9-34` |
| P1.4 | Backtest deduplication by (ticker, entry_low, target, stop) | `legacy.py` |
| P1.5 | Fit agent calibration weights from observations.jsonl | `scripts/fit_agent_weights.py` (new) |
| P1.6 | RSI hard reject -> 70; minimum volume surge 0.30x | `config.py:94`, `pipeline.py:476-495` |
| P1.7 | Chartist -> flash_llm | `debate_chamber.py:2300` |
| P1.8 | Devil's advocate vote extraction | `debate_chamber.py:3003-3033` |
| P1.9 | Backtest horizon -> 20 trading days + timeout_flat label | `backtest_outcome_evaluator.py:20` |
| P1.10 | MAX_TARGET_RETURN -> 10%; update cio_judge.txt header | `debate_chamber.py:3189`, `cio_judge.txt:1` |

### P2 — Strategic (1-2 Months)

| # | Task | Value |
|---|---|---|
| P2.1 | Structural stop: below nearest swing low + regime ATR buffer | Prevents stop-noise without over-widening |
| P2.2 | Bull/Bear R1 prompt expansion with mandatory 3-citation requirement | Makes multi-round debate converge, not repeat |
| P2.3 | Portfolio heat cap (6% open risk) + drawdown kill-switch (15% in 30d -> force DEFENSIVE) | Prevents batch-entry into full corrections |
| P2.4 | Historical backtest engine: replay screener + envelope + governor on 2-3 yr OHLCV | Answers definitively: does the strategy have edge? |
| P2.5 | Ablation study: screener-only vs scout+envelope vs full debate | Determines if 4x debate cost is justified by accuracy |
| P2.6 | Minimum volume confirmation before recording a trade entry | Fixes fill assumption bias in backtest measurement |

---

## Appendix A — Observed Run Details

### Screened Candidates (scratch/report.md, 2026-06-11 11:11)
| Rank | Ticker | Score | Vol Surge | FV Gap | Issue |
|---|---|---|---|---|---|
| 1 | DMAS | 67.5 | 0.16x | +18.3% | Volume anemia; 84% below avg |
| 2 | MPMX | 59.5 | 1.34x | +12.1% | Only near-normal volume candidate |
| 3 | PSAB | 57.5 | 0.22x | +9.4% | Volume anemia |
| 4 | MAPI | 56.5 | 0.45x | +7.2% | Volume anemia |
| 5 | BMRI | 44.5 | 0.31x | +5.1% | Large-cap, still sub-average |
| 6 | INDO | 40.5 | 0.28x | +2.8% | Volume anemia |
| 7 | ACES | 39.5 | 0.33x | +1.9% | Volume anemia |
| 8 | HATM | 30.5 | 0.41x | 0.0% | Price above Graham FV; penalty insufficient to block |
| 9 | TLKM | 24.5 | 0.52x | 0.0% | Price above Graham FV |
| 10 | RAJA | 19.0 | 0.38x | 0.0% | Price above Graham FV |

### Full Pipeline Result (output/TOP_3_SWING_TRADES.md, 2026-06-11 14:48)
```
Stocks Debated: 1 | Eligible (BUY/STRONG_BUY): 0 | Selected: 0
```
Root cause unconfirmed. Standalone `uv run idx debate DMAS MPMX PSAB MAPI BMRI` ran all 5 without errors (22m 55s). Budget exhaustion is one candidate; quant filter or batch-file mismatch is another. Requires log inspection for run_id `20260611_144852`.

### Standalone Debate Run (uv run idx debate, 2026-06-11 16:30)
| Ticker | Rating | Conviction | R/R | Price |
|---|---|---|---|---|
| DMAS | HOLD | 58% | 2.00x | Rp 151 |
| MPMX | HOLD | 60% | 2.00x | Rp 925 |
| PSAB | AVOID | 15% | 2.67x | Rp 550 |
| MAPI | HOLD | 60% | 2.00x | Rp 1,485 |
| BMRI | HOLD | 60% | 2.21x | Rp 4,250 |

**Observations:** (1) 3 of 5 R/R values exactly 2.00x — suggests target cap or stop floor is binding every time. (2) HOLD bias consistent across all candidates regardless of fundamentals. (3) Zero BUY production is appropriate given current NEUTRAL/DEFENSIVE market, but the 2.00x R/R clustering warrants investigation — it may indicate stop-target geometry is hitting a fixed constraint rather than reflecting per-ticker risk.

### Realized Backtest Evidence (backtest_memory.jsonl)
| Metric | Value |
|---|---|
| Closed records | 80 (73 HOLD-as-trade, 7 BUY) |
| Win / Loss (all records) | 1 / 79 (1.25%) |
| Win / Loss (BUY-only) | 1 / 6 |
| Loss cause | 78x stop_hit, 1x same-day target+stop |
| Avg PnL closed | -3.29% |
| Avg days to stop | 1.6 days (75/79 within 3 days) |
| Entry date range | 2026-05-31 to 2026-06-11 (entirely in DEFENSIVE period) |
| Only "win" | NZIA +37.3% in 1 day — pre-fix era (52w-high fantasy target), not repeatable edge |

**Corrected interpretation:** Remove HOLD-as-trade bias, dedup multi-run records, exclude the pre-fix NZIA anomaly: 0W/6L in BUY-only over 12 days during a crash. Small sample, adverse market period. The mechanism of loss (stop-noise + counter-trend entries) is structurally clear, not noise.

---

## Appendix B — Already-Fixed Issues (rr-sanity sessions, prior to this audit)

| Fix | Issue | Session |
|---|---|---|
| FV quality gate | Reject FV when net_margin > 100% or only 1 valid method | rr-sanity-v1 |
| FV blend ceiling bug | Target inflated by (target + FV)/2 to above FV itself | rr-sanity-v1 |
| Swing cap | 52w-high resistance target inflating R/R to 22x (INDO) | rr-sanity-v2 |
| R/R implausibility ceiling | Governor hard-reject R/R >= 5.0 | rr-sanity-v2 |
| Voting consensus clamp | CIO could issue BUY when vote majority said HOLD | rr-sanity-v3 |
| Tent R/R conviction score | Historical scorer tent misaligned with governor floor | rr-sanity-v3 |

The engineering foundation is sound. These fixes addressed the most egregious trading logic errors. The open issues in this audit are what remains after those improvements, and they represent a system that is currently over-trading in adverse conditions with insufficient stop distances.
