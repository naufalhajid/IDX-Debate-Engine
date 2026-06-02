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

### Known limitation (not fixed here)
DSSA's R/R 9.22x is partly an artifact: when `fair_value_rejected` is set (RAG
could not verify the Graham number), the envelope receives `fair_value=0`, so
the FV-blend target ceiling is skipped and the target jumps to the 52-week high
(Rp 1,030), inflating R/R. Meanwhile the display + CIO "overvalued" reasoning
still use FV Rp 304. The overvalued flag and the R/R are computed from
contradictory fair-value assumptions. See assessment notes for follow-ups.
