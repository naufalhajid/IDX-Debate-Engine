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
