# Prompt Migration Log

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
