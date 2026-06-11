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
