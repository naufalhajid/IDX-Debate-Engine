# REMEDIATION_BACKLOG.md

Strategic alignment task backlog derived from `STRATEGIC_ALIGNMENT_AUDIT.md` (2026-06-21).
All 7 areas verified against current code before task entry. No source files were modified
during this pass.

---

## Executive Summary

Seven misalignments were confirmed across the debate engine. Three are structural (the code
does what it should *not* do for swing trading); four are gaps or weak defaults. The two
highest-impact issues — fair-value ceiling on trade targets and inverted target seeding —
both live in the same function and should be addressed in a single coordinated change.

**Critical (breaks swing-trading logic):**
- Task A — FV ceiling collapses swing targets for trending momentum stocks
- Task B — Target seeding starts at R/R floor, not at resistance (logic is backwards)

**High (material alignment gap):**
- Task C — DDM perpetuity formula inside a 3–15 day fair-value composite
- Task D — R/R minimums don't scale with regime (stops do; thresholds don't)
- Task E — FAIL/PASS conflict matrix gatekeeps momentum plays behind foreign-flow data

**Refinement (additive improvements, lower blast radius):**
- Task F — Risk Governor has no liquidity gate; direct `debate` path bypasses quant-filter ADT check
- Task G — Calendar-event gap risk is advisory-only (ex-date), not enforced by governor

---

## Status Tracking (verified against code, 2026-07-09)

All seven tasks have shipped. Verified by direct code inspection (grep + read), not
by assuming the plan was executed.

| Task | Status | Evidence in code |
|---|---|---|
| A — Remove FV ceiling from trade targets | ✅ Done | `debate_chamber.py` `_compute_trade_envelope` — target is resistance-based with a sector-aware swing cap only (`_SECTOR_MAX_TARGET`); no fair-value ceiling remains in the target path |
| B — Resistance-first target seeding | ✅ Done | `debate_chamber.py` (~line 3886) — resistance candidates (20d/50d/52w ≤ 1.30× price) are collected first; the 2.0x R/R seed is only the fallback when none qualify |
| C — Reduce DDM weight in swing FV composite | ✅ Done | `fair_value_calculator.py` `SECTOR_WEIGHTS` (~line 842) — DDM weight is 0.00–0.05 across all sector profiles |
| D — Regime-aware R/R minimums | ✅ Done | `utils/trade_math.py` — `REGIME_RR_SCALING` applied inside `get_rr_minimum()` |
| E — Relax FAIL/PASS matrix for momentum | ✅ Done 2026-06-21 | `PROMPT_MIGRATION.md` — `taskE-failpass-v19` |
| F — Liquidity gate in Risk Governor | ✅ Done | `risk_governor.py` — "Task F" ADT gate: hard-reject < Rp 2B, soft-flag < Rp 10B (`ADT_HARD_REJECT_THRESHOLD_IDR`) |
| G — Calendar-event gap risk enforced | ✅ Done | `risk_governor.py` — ex-date tiers enforced deterministically (`exdate_imminent` reject, `exdate_cap65` confidence cap) |

Note: R/R floor constants referenced in Task D have since moved to 1.4x large-cap /
1.62x default (transaction-cost adjustment C7, `CALCULATION_AUDIT_TASKS.md`, 2026-06-22).

---

## Sequencing

```
Week 1: Task A + Task B  (same function, one commit)
         → update tests that currently assert old FV-ceiling behavior before touching code

Week 2: Task D           (R/R regime scaling — write tests first, then change constants + get_rr_minimum)
         Task C           (DDM weight reduction — isolated to fair_value_calculator.py, no test update needed)

Week 3: Task E           (prompt change — bump manifest, document in PROMPT_MIGRATION.md)

Week 4: Task F           (additive governor check — new test, then new guard)
         Task G           (calendar sizing — design review on scope before any code)
```

Dependencies:
- A and B must land together; B alone would leave a broken FV-ceiling path
- D depends on no other task but its own new test suite
- E is prompt-only; can be done any time after A/B land (so CIO sees correct prices)
- F and G are independent of each other and of A–E

---

## Tasks

---

### Task A — Remove fair-value ceiling from trade-target computation

**Priority Group:** CRITICAL

**File:** `services/debate_chamber.py`
**Verified Lines:** 3797–3799

```python
# CONFIRMED — current code:
if fair_value and fair_value > 0 and target > fair_value:
    target = snap_to_tick(fair_value)
    target_basis += " (FV Ceiling)"
```

**Finding:**
The trade-target ceiling is capped at fair value, collapsing momentum targets to the
fair-value anchor. A stock breaking out above its DCF/PBV composite is exactly the
scenario where a swing trader enters — the FV ceiling is both wrong in direction and
wrong in timeframe (perpetuity metrics vs. a 3–15 day setup). Real example: INDO at
Rp 165, FV Rp 253, legitimate swing target Rp 192 — the ceiling fires unnecessarily.

**Proposed Change:**
Remove the three-line FV ceiling block (lines 3797–3799) from `_compute_trade_envelope()`.
The swing-cap ceiling (lines 3806–3809, sector-aware `_max_target_return`) and the
`RR_IMPLAUSIBLE_CEILING` hard reject in the risk governor already bound unrealistic targets.
The separate preflight use of `fv_ceiling` at line ~3533 (which checks
`fundamental_ok = current_price <= fv_ceiling`) is a different code path and must NOT be
touched by this change.

**Test Coverage — Status: COVERED (existing tests assert current wrong behavior)**
Existing tests that must be updated before changing the code:
- `tests/test_debate_chamber_reliability.py::test_trade_envelope_keeps_target_above_entry_after_low_fair_value_blend` — asserts FV collapse → rejection; update to assert swing-cap fires instead when FV < entry
- `tests/test_debate_chamber_reliability.py::test_trade_envelope_fair_value_is_hard_ceiling_not_blend` — asserts `(FV Ceiling)` in target_basis; update to assert swing-cap fires or target is uncapped
- `tests/test_debate_chamber_reliability.py::test_trade_envelope_tick_fallback_preserves_ceiling_provenance` — asserts `FV Ceiling` in rejection reason; update expected rejection path
- `tests/test_debate_chamber_reliability.py::test_trade_envelope_swing_cap_applies_even_with_fair_value_above_resistance` — this one already tests the swing cap; confirm it still passes after removal

New tests needed:
- Test that a breakout stock (current_price > fair_value) now gets a target based on
  resistance/swing-cap, not truncated at fair_value
- Test that swing-cap ceiling still fires when target exceeds sector `_max_target_return`

**Risk:** HIGH — changes behavior of every computed envelope; all envelope-path tests must
be verified green before merge.

**Effort:** S (3 lines to remove) but M including test updates (4 tests to rewrite)

---

### Task B — Fix inverted target seeding (resistance first, then R/R check)

**Priority Group:** CRITICAL

**File:** `services/debate_chamber.py`
**Verified Lines:** 3763–3789

```python
# CONFIRMED — current logic (condensed):
rr_target   = entry_high + (risk_from_entry_high * 2.0)   # 2.0x R/R seed
min_target  = entry_mid * 1.04                             # 4% minimum gain
target_cand = max(rr_target, min_target)
if high_20d >= target_cand:     target_cand = high_20d
elif high_50d >= target_cand:   target_cand = high_50d
elif high_52w >= target_cand and high_52w <= current_price * 1.30:
                                target_cand = high_52w
# else: stay at 2.0x R/R seed — never a natural resistance level
```

**Finding:**
The algorithm builds a 2.0x R/R floor first, then looks for resistance *above* that floor.
Resistance is ignored when it lies between current_price and the 2.0x seed. A valid swing
setup (e.g., entry 1000, next resistance 1040, stop 960) yields a target of 1040 only when
1040 >= entry_high + 2.0×40 = 1080 — which is false. The target defaults to the 2.0x
synthetic seed instead of the actual resistance level, and the setup either fires with an
artificial target or gets a worse (too-high) one if resistance is not found.

**Proposed Change:**
Invert the priority: collect the three resistance candidates first (high_20d, high_50d,
high_52w with the existing 130% cap), pick the nearest one above entry_high, then compute
the implied R/R. If no resistance is found within reasonable range, fall back to the 2.0x
seed (preserving the existing safety floor). This makes resistance the primary signal and
R/R the acceptance criterion, not the other way around. Minimum-gain floor (4%) and
`RR_IMPLAUSIBLE_CEILING` (5.0x) guards remain.

**Test Coverage — Status: PARTIAL**
Existing coverage:
- `tests/test_debate_chamber_reliability.py::test_trade_envelope_guarantees_sufficient_rr_from_entry_high`
  confirms the 2.0x seed exists and the envelope doesn't ship R/R < 2.0 — will need updating

New tests needed before refactoring:
- Test: when high_20d is between entry_high and the 2.0x seed, target = high_20d (resistance wins)
- Test: when no resistance is found, target falls back to 2.0x seed (safety floor preserved)
- Test: high_52w > 130% of current_price is still ignored after inversion
- Test: R/R < 1.3 after resistance selection → envelope rejected (not silently accepted)

**Risk:** HIGH — core target-computation logic; must run full `test_debate_chamber_reliability.py`
after change.

**Effort:** M — logic restructure, not a line delete; ~30 lines affected + new tests

---

### Task C — Reduce or remove DDM weight from swing fair-value composite

**Priority Group:** HIGH

**File:** `services/fair_value_calculator.py`
**Verified:** CONFIRMED — SECTOR_WEIGHTS dict includes `"ddm": 0.20` (default), `"ddm": 0.20` (bank),
`"ddm": 0.20` (consumer), `"ddm": 0.15` (property), `"ddm": 0.05` (mining).
DDM formula: Gordon Growth Model perpetuity `dps / (ke - g)`.

**Finding:**
The Gordon Growth Model prices a stock as a perpetuity of dividends discounted to
infinity. This is a 10–30 year intrinsic-value lens. On a 3–15 day swing trade, dividend
yield and terminal growth rate have zero short-term predictive power. A 20% DDM weight
inside the "fair value" that is used in the conflict-signal preflight (`fundamental_ok`)
and in the FV ceiling (Task A) pulls the composite toward perpetuity value and away from
near-term catalysts. The data quality gate (`confidence == "LOW"` → null anchor) already
handles cases where DDM data is missing; reducing the weight does not break that path.

**Proposed Change (two options, choose one):**
Option 1 (preferred): Zero out DDM weight and redistribute to PE/PBV. Example default:
`"default": {"pe": 0.55, "pb": 0.45, "ddm": 0.00}`. Mining sector keeps DDM at 0 (it
already has only 5%). Bank/property sectors keep a small DDM weight (max 5%) since
dividend yield is a genuine sector signal for those.

Option 2: Replace DDM with the already-implemented `_compute_valuation_band_context()`
signal (HISTORICALLY_CHEAP / BELOW_AVG / ABOVE_AVG / HISTORICALLY_EXPENSIVE) as a tiebreaker
in the composite weight rather than a price-based perpetuity model. This uses relative
historical PE/PBV percentile, which is more swing-relevant than perpetuity income.

**Uncertainty:** Whether Option 2 is feasible depends on how `_compute_valuation_band_context`
is consumed downstream. Confirm it is not already double-counted before adding it as a
composite input.

**Test Coverage — Status: PARTIAL (needs new coverage before changing weights)**
Existing tests in `tests/test_fair_value_calculator.py` cover:
- `test_sector_weight_assertion` — asserts current weights; must be updated after change
- DDM calculation math is not directly tested (no `test_*ddm*` found)

New tests needed:
- Test that composite FV for a zero-DPS stock is not penalized by a DDM = 0 case (currently
  DDM returns None when DPS = 0, which silently drops the weight — confirm behavior is
  preserved)
- Test that bank sector DDM weight <= 5% in new config

**Risk:** MEDIUM — isolated to `fair_value_calculator.py`; no debate logic changes needed.
FV values will shift for all tickers, which shifts the `fundamental_ok` preflight signal.
Run regression across test suite after change.

**Effort:** S (dict constant change) but M including downstream validation

---

### Task D — Make R/R minimums regime-aware (match ATR-stop scaling)

**Priority Group:** HIGH

**File:** `utils/trade_math.py`
**Verified Lines:** Flat constants confirmed:
```python
LARGE_CAP_RR_MINIMUM: float = 1.3
DEFAULT_RR_MINIMUM:   float = 1.5
```
`get_rr_minimum(ticker, yf_info=None)` — no `regime` parameter.

Contrast confirmed at `utils/technicals.py`:
```python
REGIME_ATR_STOP_MULTIPLIER = {
    "LOW": 2.5, "NORMAL": 2.5, "HIGH": 2.5, "RECOVERY": 2.5, "DEFENSIVE": 3.0
}
```
Stops widen in DEFENSIVE regime. Thresholds do not.

**Finding:**
ATR stops scale 20% wider in DEFENSIVE regime — meaning risk per trade increases. But the
R/R minimum stays flat, so the same 1.5x threshold applies whether the market is in a calm
LOW regime or in DEFENSIVE. In HIGH or RECOVERY regime, a wider stop requires a farther
target to clear the same R/R; raising the minimum in those regimes would correctly filter
setups whose targets aren't far enough to justify the elevated risk.

**Proposed Change:**
Add a `regime` parameter to `get_rr_minimum(ticker, regime=None, yf_info=None)`.
When regime is provided, apply a scaling dict analogous to REGIME_ATR_STOP_MULTIPLIER:
```python
REGIME_RR_SCALING: dict[str, float] = {
    "LOW":       1.0,   # no change to base floor
    "NORMAL":    1.0,
    "HIGH":      1.2,   # default floor becomes 1.8x
    "RECOVERY":  1.1,   # default floor becomes 1.65x
    "DEFENSIVE": 1.3,   # default floor becomes 1.95x
}
```
Callers in `core/risk_governor.py` and `services/debate_chamber.py` that already have
regime available should pass it. Callers without regime continue with `regime=None`
(backward-compatible, same floors as today).

**Uncertainty:** Need to verify which callers of `get_rr_minimum` already have access to
the current regime value. If they receive it via `candidate["regime"]` or `settings`, the
wire-up is trivial. If regime is not in scope at the call site, a small threading change
is needed.

**Test Coverage — Status: COVERED for current behavior; NEEDS NEW COVERAGE before change**
Existing tests in `tests/test_trade_math.py` assert flat threshold behavior and do NOT
need to be deleted — the new `regime` parameter is additive with a default of `None`.

New tests needed (write first):
- `test_get_rr_minimum_high_regime_applies_scaling` — HIGH regime → 1.5 × 1.2 = 1.8
- `test_get_rr_minimum_defensive_regime_applies_scaling` — DEFENSIVE → 1.5 × 1.3 = 1.95
- `test_get_rr_minimum_none_regime_unchanged` — None → 1.5 (backward compat preserved)
- `test_get_rr_minimum_large_cap_high_regime` — large-cap base × HIGH → 1.3 × 1.2 = 1.56

**Risk:** LOW — additive parameter with default preserves all existing behavior; callers
that do not pass regime are unaffected.

**Effort:** S (constant dict + parameter) + M for finding all call sites and wiring regime

---

### Task E — Relax FAIL/PASS conflict matrix for momentum setups

**Priority Group:** HIGH

**File:** `services/debate_prompts/cio_judge.txt`
**Verified Lines:** 83–91

```
Fundamental + Technical == FAIL/PASS -> IF strongly positive Foreign Flow AND Volume breakout
                                          -> Lean BUY (Momentum Play, size 50%).
                                       ELSE -> HOLD.
```

**Finding:**
A FAIL/PASS setup (technicals strong, fundamentals weak) is a common, valid momentum
swing-trade scenario on IDX. Requiring *both* strongly positive foreign flow AND volume
breakout creates a compound gate that excludes most small/mid-caps (foreign flow data is
thin or absent for non-LQ45 members). The current logic treats any momentum setup without
foreign flow as "wait for confirmation," blocking the most frequent valid setup type.

**Proposed Change (prompt edit):**
Revise the FAIL/PASS row to require volume breakout as the primary gate, with foreign flow
as a confidence booster — not a blocker:

```
Fundamental + Technical == FAIL/PASS ->
  IF Volume breakout confirmed:
    -> Lean BUY (Momentum Play, size 50%).
    -> IF also strongly positive Foreign Flow: raise size to 75%.
    -> State explicitly: Momentum trade — entry is purely technical, no fundamental support.
  ELSE (no volume breakout):
    -> HOLD ("Wait for volume confirmation before momentum entry")
```

After editing, bump `prompt_version` in `services/debate_prompts/manifest.json` and
document in `PROMPT_MIGRATION.md` per project convention.

**Test Coverage — Status: NEEDS COVERAGE before change**
No behavioral tests found that exercise the FAIL/PASS conflict path specifically.

New tests needed (write first):
- Add a `test_debate_chamber_reliability.py` test that feeds a FAIL/PASS conflict signal
  WITH volume breakout → expects Lean BUY rating
- Add a test that feeds FAIL/PASS WITHOUT volume breakout → expects HOLD
- Verify prompt-pack linter (`tests/test_prompt_pack_linter.py`) still passes after edit

Consider also adding a deterministic post-processing check in `_extract_cio_verdict()`
that caps confidence at 0.65 for any FAIL/PASS-to-BUY verdict, to limit LLM deviation.

**Risk:** MEDIUM — prompt-only change, no Python logic modified. Risk is LLM compliance
drift between prompt intent and actual CIO output.

**Effort:** S (prompt text edit) + M for compliance test

---

### Task F — Add liquidity gate to Risk Governor for direct `debate` path

**Priority Group:** REFINEMENT

**Files:**
- `core/risk_governor.py` — `evaluate_risk()` (~lines 100–326), confirmed no volume/turnover check
- `core/quant_filter/pipeline.py` lines 621–625 — ADT gate exists here (`min_adt_20d: 10B`)

**Finding:**
When `uv run idx pipeline` runs, the quant-filter's ADT gate (Rp 10B average daily
turnover) screens out illiquid stocks before debate. When `uv run idx debate BBCA BBRI`
is called directly, that gate is bypassed entirely — the debate CLI routes to
`run_debate.main` without calling the quant-filter (`app/cli/commands/debate.py`). The
risk governor can issue `deployable` for a stock with Rp 200M daily turnover.

**Uncertainty:**
`adt_20` is computed in `core/quant_filter/pipeline.py:622` as
`float((close * vol).tail(20).mean())`. It is NOT currently passed through the debate
context dict to the risk governor. Two implementation options:
1. Compute ADT independently inside `evaluate_risk()` from price/volume history if available
2. Compute ADT in the debate chamber's technical step and pass it through the candidate dict

Option 2 is cleaner but requires verifying that `market_data["history"]` is accessible
in the `evaluate_risk()` call path. Do not implement until data availability is confirmed.

**Proposed Change:**
Add a soft flag `liquidity_warning` and a hard reject `insufficient_liquidity` to
`evaluate_risk()`:
- Hard reject: `adt_20 < 2_000_000_000` (Rp 2B — floor below which fills are impractical)
- Soft flag: `2B <= adt_20 < 10_000_000_000` (Rp 10B) → `conditional_deployable` with reason `"low_liquidity"`
- No ADT data → degrade gracefully (log warning, do not gate)

**Test Coverage — Status: NEEDS COVERAGE before change**
`tests/test_risk_governor.py` has no liquidity-related tests.

New tests needed (write first):
- `test_illiquid_ticker_hard_rejects` — ADT < 2B → status "reject", code "insufficient_liquidity"
- `test_low_liquidity_ticker_conditional` — 2B <= ADT < 10B → status "conditional_deployable"
- `test_liquid_ticker_unaffected` — ADT >= 10B → existing behavior unchanged
- `test_missing_adt_degrades_gracefully` — no ADT field → no gate applied, warning logged

**Risk:** LOW — additive guard; all existing tests unaffected if ADT field defaults to None.

**Effort:** M — data-flow trace from debate_chamber to risk_governor + new tests

---

### Task G — Enforce calendar-event gap risk in governor (beyond ex-date advisory)

**Priority Group:** REFINEMENT

**Files:**
- `services/debate_chamber.py` lines 112–126 — `_compute_exdate_gate()` exists, produces AVOID/CAP_65/MONITOR/CLEAR
- `core/risk_governor.py` — confirmed: no enforcement of exdate_gate or any calendar event sizing

**Current state confirmed:**
`_compute_exdate_gate()` produces advisory text passed to the CIO judge prompt. The
governor does NOT read this field. No BI rate meeting, earnings window, or macro event
calendar exists anywhere in the codebase.

**Finding:**
Ex-date gap risk is advisory-only — if the CIO ignores or misreads the AVOID signal, a
`deployable` verdict can be issued for a stock 3 days before ex-date. Calendar-event scope
is also limited to dividend ex-dates only; BI rate decisions (8 per year) and IDX earnings
windows are not modeled.

**Proposed Change (two phases):**

Phase 1 (implement now): Read `exdate_gate` inside `evaluate_risk()` and enforce
deterministically:
- AVOID → hard reject with code `"exdate_imminent"` (override LLM)
- CAP_65 → cap `confidence` at 0.65 in the returned `RiskDecision` if CIO went higher

Phase 2 (design review required before implementation): Add a `calendar_events` field to
the candidate context (BI rate decisions, earnings windows) and extend `evaluate_risk()` to
flag `binary_event_risk` → `conditional_deployable` with mandatory size reduction.

**Uncertainty:**
Phase 1 requires confirming that `exdate_gate` (or raw `exdate_info`) is present in the
`candidate` dict that reaches `evaluate_risk()`. If not, a small data-threading change is
needed.

Phase 2 scope and external data source must be decided before any code is written.

**Test Coverage — Status: NEEDS COVERAGE**
`tests/test_risk_governor.py` has no exdate-related tests.

New tests needed (Phase 1 only):
- `test_exdate_avoid_hard_rejects` — exdate_gate = AVOID → reject, code "exdate_imminent"
- `test_exdate_cap65_reduces_confidence` — exdate_gate = CAP_65, CIO confidence 0.85 → 0.65
- `test_exdate_clear_unaffected` — exdate_gate = CLEAR → existing behavior unchanged

**Risk:** LOW for Phase 1 (enforcing an already-computed field). HIGH for Phase 2 (scope
creep; do not start Phase 2 without explicit design sign-off).

**Effort:** Phase 1 — S (guard + tests). Phase 2 — XL (external data dependency).

---

## Verification Notes

| Area | Verified | Confirmed Location | Uncertainty |
|---|---|---|---|
| FV ceiling on target | YES | `debate_chamber.py` L3797–3799 | None |
| Inverted target seeding | YES | `debate_chamber.py` L3763–3789 | None |
| DDM in FV composite | YES | `fair_value_calculator.py` SECTOR_WEIGHTS | Option 2 feasibility (valuation_band as substitute) |
| Flat R/R thresholds | YES | `trade_math.py` constants + `get_rr_minimum` signature | Which callers have regime in scope |
| FAIL/PASS conflict matrix | YES | `cio_judge.txt` L83–91 | LLM compliance post-change |
| Liquidity gate gap | YES (partial) | `risk_governor.py` — absent; `quant_filter/pipeline.py` L621–625 — present for pipeline path only | ADT data availability in debate-direct path |
| Gap risk scope | YES (partial) | `debate_chamber.py` L112–126 exdate advisory only | Phase 2 data source for earnings/BI calendar |

---

*Read-only pass — no source files modified. All code references verified by direct Grep
against the current working tree on 2026-06-21.*
