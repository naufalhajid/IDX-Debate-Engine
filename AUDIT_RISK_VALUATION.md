# Focused Audit — Risk & Valuation Layer

> **Scope:** R/R Threshold · Target Price Methodology · Fair Value Threshold · Risk Governor
> **Date:** 2026-06-20
> **Status:** Draft — pending fixes

---

## Component 1 — R/R Threshold

**Formula (exact code — `utils/trade_math.py:38-46`):**

```python
risk   = entry_high - stop          # worst-case fill basis ✓
reward = target - entry_high
return round(reward / risk, 2)      # (Target - EntryHigh) / (EntryHigh - Stop)
```

Mathematically correct. Entry_high as worst-case fill is the right conservative choice.

**Thresholds:** 1.3× large cap (≥ Rp 50T market cap), 1.5× default. Plausibility ceiling 5.0×. Tier-aware. ✓

---

### R/R ISSUE 1: `low_confidence` is computed but never enforced as a deployment blocker

**Severity:** 🟠 Medium

**Location:** `core/risk_governor.py:454-455`, `HARD_REJECT_CODES:34-41`

**Problem:** When CIO verdict confidence < 0.60, `low_confidence` is appended to `reason_codes`. But `low_confidence` is not in `HARD_REJECT_CODES` and `_is_conditional_setup()` (line 725) only checks for `rating_hold` and `counter_trend_setup`. A BUY signal with confidence = 0.35 gets `sizing_allowed=True` when price is inside the entry zone.

**Risk:** A coin-flip conviction level (35%) signal gets deployed with full position sizing. The LLM's own uncertainty estimate is logged but has zero effect on execution.

**Fix:**

```python
# BEFORE (core/risk_governor.py:34-41)
HARD_REJECT_CODES = {
    "rating_not_buyable",
    "overvalued",
    "rr_too_low",
    "rr_implausible",
    "insufficient_technical_data",
    "ara_entry_risk_high",
}

# AFTER
HARD_REJECT_CODES = {
    "rating_not_buyable",
    "overvalued",
    "rr_too_low",
    "rr_implausible",
    "insufficient_technical_data",
    "ara_entry_risk_high",
    "low_confidence",          # confidence < 0.60 must block sizing, not just log
}
```

---

### R/R ISSUE 2: LLM-provided `risk_reward_ratio` in verdict is trusted over recomputed value

**Severity:** 🟡 Low-Medium

**Location:** `core/risk_governor.py:463-465`

**Problem:** The governor first reads `verdict.get("risk_reward_ratio")` and only falls back to `_recompute_rr()` if that is None. The LLM CIO judge is free to compute R/R from `entry_mid` instead of `entry_high`, producing an inflated ratio.

**Example:** entry_high=1000, target=1200, stop=900 → real R/R = (200/100) = **2.0×**; LLM computes from entry_mid=950 → (250/50) = **5.0×** → appears implausible but passes the ≥ 1.5× gate with an inflated number.

**Risk:** Inflated LLM-calculated R/R passes the `≥ 1.5×` gate when the actual worst-case R/R is below threshold.

**Fix:**

```python
# BEFORE (core/risk_governor.py:463-465)
rr_ratio = _first_float(verdict.get("risk_reward_ratio"))
if rr_ratio is None:
    rr_ratio = _recompute_rr(ticker, entry_high, target_price, stop_loss)

# AFTER — always recompute; use verdict only as fallback when prices missing
rr_ratio = _recompute_rr(ticker, entry_high, target_price, stop_loss)
if rr_ratio is None:
    rr_ratio = _first_float(
        verdict.get("risk_reward_ratio"),
        candidate.get("risk_reward_ratio"),
    )
```

---

## Component 2 — Target Price Methodology

**Target construction flow (`_compute_trade_envelope`, `services/debate_chamber.py:3736-3788`):**

1. **Seed:** `rr_target = entry_high + risk × 2.0` (2× R/R baseline from entry_high)
2. **Floor:** `entry_mid × 1.04` (minimum 4% from mid)
3. **Resistance bump:** `high_20d → high_50d → high_52w` if above baseline
4. **Ceiling 1:** hard cap at `fair_value`
5. **Ceiling 2:** hard cap at `entry_high × 1.10` (`MAX_TARGET_RETURN = 0.10`)
6. **Fallback:** if `target ≤ entry_high` → `_next_tick_above(entry_high)`

---

### TARGET ISSUE 1: 52-week high as swing resistance is stale and produces misleading `target_basis` label

**Severity:** 🟠 Medium

**Location:** `services/debate_chamber.py:3761-3762`

**Problem:** `elif high_52w >= target_candidate: target_candidate = high_52w; target_basis = "Resistance 52-Week"`. A 52-week high from 10 months ago (stock crashed since) is treated as relevant resistance for a 3–15 day swing. It then gets swing-capped at +10%, but `target_basis` still reads "Resistance 52-Week" — the label survives the cap and falsely implies a real chart level supports the target.

**Risk:** Analyst reads "Resistance 52-Week" and believes the target is anchored to a real, nearby technical level when it is actually a swing-cap derived from an irrelevant stale high.

**Fix:**

```python
# BEFORE (services/debate_chamber.py:3761-3762)
elif high_52w >= target_candidate:
    target_candidate = high_52w
    target_basis = "Resistance 52-Week"

# AFTER — only use 52W high if within realistic reclaim distance
elif high_52w >= target_candidate and high_52w <= current_price * 1.30:
    target_candidate = high_52w
    target_basis = "Resistance 52-Week"
# beyond 30% reclaim distance: stale for a swing trade; baseline R/R target stands
```

---

### TARGET ISSUE 2: `MAX_TARGET_RETURN = 0.10` is universal — mining/energy setups systematically capped at half their realistic range

**Severity:** 🟡 Low-Medium

**Location:** `services/debate_chamber.py:3643, 3779`

**Problem:** A flat 10% swing cap applies to BYAN, ADRO, MDKA identically to BBCA or TLKM. IDX mining stocks routinely swing 15–25% in commodity momentum runs. With a 5–8% structural stop (1–1.5× ATR), a 10% cap produces R/R of 1.25–2.0× — borderline or barely above the 1.5× minimum. For large R/R opportunities the system is leaving value on the table and systematically producing near-threshold setups.

**Risk:** Mining setups exit too early relative to their volatility; or they get rejected as borderline R/R when a sector-adjusted cap would produce a clean 2.0× setup.

**Fix:**

```python
# BEFORE (services/debate_chamber.py:3643)
MAX_TARGET_RETURN = 0.10

# AFTER — define per-sector caps
_SECTOR_MAX_TARGET = {
    "mining": 0.20,
    "consumer": 0.12,
    "property": 0.15,
    "bank": 0.10,
    "default": 0.12,
}
# In __init__: self.MAX_TARGET_RETURN = _SECTOR_MAX_TARGET.get(self._sector, 0.12)
```

---

### TARGET ISSUE 3: Collapsed target (FV ceiling → 1 tick above entry) generates a trade that will always be hard-rejected — compute waste and latent risk

**Severity:** 🟠 Medium

**Location:** `services/debate_chamber.py:3784-3788`

**Problem:** When FV is very close to entry, both FV ceiling and swing cap collapse `target ≤ entry_high`. The fallback at line 3784 sets `target = _next_tick_above(entry_high)` (e.g. 25 IDR above a 5000 IDR stock = 0.5% upside). `calculate_rr()` at line 3797 then returns ≈ 0.05×. The envelope is returned and fed to LLM debate agents, scoring, and reporting before the risk governor rejects it as `rr_too_low`. All that compute runs for a trade that was geometrically doomed before debate started.

**Risk:** If the `rr_too_low` check is ever bypassed (see GOVERNOR ISSUE 1), a 0.05× R/R trade reaches sizing. Even correctly rejected, it burns a full LLM debate cycle per ticker.

**Fix:**

```python
# BEFORE (services/debate_chamber.py:3784-3788)
if target <= entry_high:
    target = self._next_tick_above(entry_high)
    target_basis += " (Tick Increment Fallback)"

# AFTER — detect collapse early and return a structured rejection
if target <= entry_high:
    return {
        "rejected": True,
        "reason": (
            f"target_collapsed: FV/swing ceiling ({fair_value}) too close to "
            f"entry ({entry_high}); no viable upside remains"
        ),
    }
```

---

## Component 3 — Fair Value Threshold

**How overvaluation is flagged:**

- Fundamental path: `build_fair_value_payload()` → `risk_overvalued = price > fair_value_high`
- CIO path: `CIOVerdict._derive_computed_fields()` → `risk_overvalued = current_price > fair_value_high`
- Risk governor: both verdict AND candidate `risk_overvalued` checked; either truthy → hard reject ✓

**FV confidence gates:**
- LOW (< 2 methods succeed) → quality gate fires → `fair_value = None`
- MEDIUM (2 methods) → ±15% range
- HIGH (3+ methods) → ±10% range

---

### FV ISSUE 1: `fair_value = None` produces `risk_overvalued = False` — overvalued stocks with bad data pass through

**Severity:** 🔴 Critical

**Location:** `schemas/debate.py:308-317`

**Problem:** When FV is unmeasurable (quality gate fires, data corruption, < 2 FV methods succeed), the `else` branch in `_derive_computed_fields()` sets `risk_overvalued = False`. There is no distinction between "we checked and it's fairly valued" vs "we couldn't check." A stock trading at 50× PE with insufficient API data to compute FV gets `risk_overvalued = False` and passes the overvaluation gate.

**Risk:** Fundamentally expensive stocks escape the overvaluation filter precisely when data quality is worst — the scenario where automated protection matters most.

**Fix:**

```python
# BEFORE (schemas/debate.py:308-317)
def _derive_computed_fields(self) -> "CIOVerdict":
    if self.fair_value_high and self.current_price:
        self.risk_overvalued = self.current_price > self.fair_value_high
    elif self.fair_value and self.current_price:
        self.risk_overvalued = self.current_price > self.fair_value
    else:
        self.risk_overvalued = False    # treats "unknown" as "safe"
    self.is_overvalued = self.risk_overvalued
    return self

# AFTER — None means unknown; governor treats None as conditional block
def _derive_computed_fields(self) -> "CIOVerdict":
    if self.fair_value_high and self.current_price:
        self.risk_overvalued = self.current_price > self.fair_value_high
    elif self.fair_value and self.current_price:
        self.risk_overvalued = self.current_price > self.fair_value
    else:
        self.risk_overvalued = None     # unknown — governor should block deployment
    self.is_overvalued = self.risk_overvalued
    return self
```

Also update `_risk_overvalued_flag()` in `core/risk_governor.py:411-418` to treat `None` as a conditional block (not a free pass).

---

### FV ISSUE 2: C3 historical band (`HISTORICALLY_EXPENSIVE`) is advisory only — no deterministic enforcement

**Severity:** 🟡 Low-Medium

**Location:** `services/context_pack_builder.py` (tier2 field), `core/risk_governor.py` (absent)

**Problem:** C3 band context (`HISTORICALLY_EXPENSIVE` at 95th percentile of own multi-year history) flows to the LLM debate agents as a text block but is never evaluated deterministically by the risk governor. If LLM agents are overconfident or dismiss it, an HISTORICALLY_EXPENSIVE stock still gets a BUY rating and passes all hard gates.

**Risk:** A stock priced at the 95th percentile of its own 7-year PE/PBV range with a "BUY" from an LLM passes all hard gates. The quantitative signal exists but has zero enforcement.

**Fix:** Add a soft gate — when `valuation_band_context` contains `"HISTORICALLY_EXPENSIVE"`, append a `"historically_expensive"` soft-flag to `reason_codes` (conditional, not hard-reject). This ensures analysts see it in the risk output and the setup can only proceed as conditional (not fully deployable).

---

## Component 4 — Risk Governor

---

### GOVERNOR ISSUE 1: FAIL-OPEN — missing `risk_governor` key is treated as "passed"

**Severity:** 🔴 Critical

**Location:** `core/orchestrator/legacy.py:4905-4914, 4921`

**Problem:** `_risk_holds()` identifies blocked entries with:

```python
if not isinstance(risk, dict) or risk.get("sizing_allowed") is not False:
    continue  # skip → NOT in holds → proceeds to sizing
```

When `annotate_risk()` raises an exception (any reason), `entry["risk_governor"]` is never set. Then `risk = entry.get("risk_governor")` = `None`. `not isinstance(None, dict)` = `True` → `continue` → entry is absent from `holds` → **treated as passed**. The trade proceeds to position sizing with no governance.

**Risk:** A single exception in `annotate_risk()` (bad data, import error, unexpected field) causes the affected entry — and ALL subsequent entries if the loop aborts — to bypass ALL governance. Overvalued, low R/R, or AVOID-rated trades get sized.

**Fix:**

```python
# BEFORE (legacy.py:4905-4914)
def _annotate_risk_governor(top_n: list[dict]) -> None:
    for entry in top_n:
        entry.setdefault("market_regime", ORCHESTRATOR_CONFIG.get("market_regime"))
        decision = annotate_risk(entry)
        if not decision.sizing_allowed:
            logger.info(
                f"[RiskGovernor] {decision.ticker}: {decision.status} "
                f"({', '.join(decision.reason_codes)})"
            )

# AFTER — fail-closed: exception → sizing blocked
def _annotate_risk_governor(top_n: list[dict]) -> None:
    for entry in top_n:
        entry.setdefault("market_regime", ORCHESTRATOR_CONFIG.get("market_regime"))
        try:
            decision = annotate_risk(entry)
            if not decision.sizing_allowed:
                logger.info(
                    f"[RiskGovernor] {decision.ticker}: {decision.status} "
                    f"({', '.join(decision.reason_codes)})"
                )
        except Exception as exc:
            ticker = entry.get("ticker", "unknown")
            logger.error(
                "[RiskGovernor] {} annotation failed — blocking sizing: {}", ticker, exc
            )
            entry["risk_governor"] = {
                "ticker": ticker,
                "status": "reject",
                "sizing_allowed": False,
                "reason_codes": ["governor_error"],
                "message": f"Risk governor failed ({exc}); sizing blocked for safety.",
            }

# BEFORE (legacy.py:4921)
if not isinstance(risk, dict) or risk.get("sizing_allowed") is not False:
    continue

# AFTER — only skip when explicitly allowed (missing key = blocked)
if isinstance(risk, dict) and risk.get("sizing_allowed") is True:
    continue
```

---

### GOVERNOR ISSUE 2: No circuit breaker — no daily/weekly loss limit

**Severity:** 🟠 Medium

**Location:** `core/risk_governor.py` (absent entirely)

**Problem:** The governor approves each setup in isolation with no portfolio-level loss awareness. If BBRI, BBCA, and BMRI (all correlated bank stocks) are approved and all hit stop-loss the same day (IHSG drops 3%), the system continues generating new BUY signals for the next pipeline run with zero knowledge of realized losses.

**Risk:** In a trending-down market, the system systematically deploys capital into declining setups, compounding losses across multiple runs. There is no floor on correlated drawdown.

**Fix:**

```python
# Add to a portfolio-level pre-check before _annotate_risk_governor():
def _portfolio_circuit_breaker(portfolio_state: dict) -> bool:
    """Return True (block all new deployments) if daily loss exceeds threshold."""
    realized_loss_pct = portfolio_state.get("realized_loss_today_pct", 0.0)
    return realized_loss_pct >= CIRCUIT_BREAKER_DAILY_LOSS_PCT  # e.g. 0.03 (3%)
```

---

### GOVERNOR ISSUE 3: Sector concentration check absent at governor level

**Severity:** 🟠 Medium

**Location:** `core/risk_governor.py` (absent), `core/portfolio_optimizer.py:54-60`

**Problem:** `evaluate_risk()` approves each ticker independently. The sector concentration limit (`max_per_sector`) is enforced downstream in `diversify_portfolio()`, which can be bypassed if the pipeline calls `evaluate_risk()` directly (e.g. via API or single-ticker mode). Three BANK setups (BBRI + BBCA + BMRI) can all get `status="deployable"` from the governor simultaneously.

**Risk:** API consumers of `evaluate_risk()` have no sector concentration protection. During a banking sector stress event, three bank positions could all deploy and all hit stop-loss.

---

### Governor Completeness Scorecard

| Check | Implemented | Notes |
|---|---|---|
| ✅ Position sizing limit | Yes | `position_sizer.py` |
| ⚠️ Max concurrent positions | Partial | `position_sizer` only, not governor |
| ⚠️ Sector concentration limit | Partial | `portfolio_optimizer` only, bypassable |
| ❌ Daily/weekly loss limit (circuit breaker) | No | Missing entirely |
| ❌ Correlation check between signals | No | Missing entirely |
| ❌ Fail-closed on system errors | No | **Currently fail-open** |
| ✅ Governor runs deterministically | Yes | No LLM in `risk_governor.py` |
| ⚠️ Governor cannot be bypassed | Partial | Direct API calls bypass sector/position limits |
| ✅ Audit log of decisions | Yes | `_log_decision()` |

---

## Cross-Component Integration

| Interaction | Status | Notes |
|---|---|---|
| Target → R/R | ⚠️ Design gap | Target can fall below R/R min inside envelope (by design); governor catches it — but see TARGET ISSUE 3 (compute waste) and GOVERNOR ISSUE 1 (fail-open) |
| FV → Target | ✅ Works | FV is a hard ceiling on target; FV=None means no ceiling — correct |
| FV → Governor | 🔴 Broken | `risk_overvalued=False` when FV unmeasurable — both FV ceiling AND overvaluation rejection fail simultaneously on bad data (FV ISSUE 1) |
| Governor veto | 🔴 Fragile | Final veto exists but silently lost on exception (GOVERNOR ISSUE 1) |
| Confidence → Sizing | 🟠 Gap | `low_confidence` logged but never blocks (R/R ISSUE 1) |

---

## Findings Dashboard

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  FOCUSED AUDIT — RISK & VALUATION LAYER
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  R/R Threshold        2 issues    Grade: B
  Target Price         3 issues    Grade: C+
  Fair Value Threshold 2 issues    Grade: C
  Risk Governor        3 issues    Grade: D+

  Integration Issues   2

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  MOST DANGEROUS FINDING:
    GOVERNOR ISSUE 1 — Fail-open on annotate_risk() exception
    legacy.py:4921 — missing risk_governor key treated as sizing_allowed=True.
    A single runtime error silently removes the last governance layer
    from ALL subsequent entries in that pipeline run.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## Prioritized Fix List

| Priority | Component | Issue | Why Critical | Effort |
|---|---|---|---|---|
| 🔴 **P0** | Risk Governor | Wrap `annotate_risk()` in try/except; default to `sizing_allowed=False` on exception; fix `is not False` → `is True` in `_risk_holds()` | Only protection that can silently vanish; fail-open = unlimited exposure | 30 min |
| 🔴 **P1** | Fair Value | `risk_overvalued = None` when FV unmeasurable; governor treats `None` as conditional block | Overvalued + bad data = both protections fail simultaneously | 45 min |
| 🟠 **P2** | R/R | Add `low_confidence` to `HARD_REJECT_CODES` | 35% confidence BUY currently deploys at full size | 5 min |
| 🟠 **P3** | R/R | Always recompute R/R from prices; use verdict R/R as fallback only | LLM can inflate R/R by computing from mid instead of high | 15 min |
| 🟠 **P4** | Target | Return `rejected: True` early when target collapses to ≤ entry | Ends wasted LLM debate cycles; removes latent 0.05× R/R path | 15 min |
| 🟠 **P5** | Risk Governor | Add portfolio circuit breaker (daily loss ≥ 3% → halt new deployments) | No floor on correlated drawdown across pipeline runs | 2 hrs |
| 🟡 **P6** | Target | Gate 52W high usage to `high_52w ≤ current_price × 1.30` | Stale crash-high used as "resistance" for 3-day swing | 10 min |
| 🟡 **P7** | Target | Make `MAX_TARGET_RETURN` sector-aware (mining 20%, default 12%) | Mining setups systematically undershoot realistic range | 30 min |
| 🟡 **P8** | Fair Value | C3 `HISTORICALLY_EXPENSIVE` → soft `historically_expensive` reason code in governor | Quantitative signal exists but has zero enforcement | 45 min |
