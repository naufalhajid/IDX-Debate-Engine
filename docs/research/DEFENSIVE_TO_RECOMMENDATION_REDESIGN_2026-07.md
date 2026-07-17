# Redesign Proposal — Defensive Gatekeeper to Calibrated Recommender

**Status:** implementation approved for the experimental duplicate; safe
information-layer refactor implemented 2026-07-17  
**Evidence base:** [Research Ledger](RESEARCH_LEDGER_2026-07.md)  
**Validation gate:** [Shadow Mode Protocol](SHADOW_MODE_PROTOCOL.md)

**Execution handoff:** [Master Implementation Checklist](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md)

## Experimental implementation checkpoint — 2026-07-17

Implemented in the duplicate workspace without changing any trading threshold:

- a strict, versioned `recommendation-context-v1` alongside the existing
  `SignalPacket`, preserving shadow-evaluation compatibility;
- deterministic states `QUALIFIED`, `WAIT_TRIGGER`, `NEAR_MISS`,
  `SINGLE_GATE_REJECT`, `HARD_REJECT`, and `DATA_INSUFFICIENT`;
- structured blocker metrics with observed value, policy threshold, absolute
  and normalized gap, provenance, and a full-recheck trigger;
- an explicitly non-executable hypothetical setup whose schema cannot grant
  sizing;
- gate-owner instrumentation for noise, momentum, target geometry, and R/R,
  including all envelope failures instead of silently retaining only the first;
- persistence through trade setup, final execution decision, execution funnel,
  API, CLI, Markdown, Rich output, and artifact validation;
- conditional methodology: zero-call preflight artifacts no longer claim that
  five agents debated or that a CIO model produced `HOLD / 0%`;
- terminology correction from “IDX4 factor model” to “IDX4-inspired stock
  characteristics” in current code/documentation surfaces.

Explicitly unchanged:

- the R/R floor, momentum logic, SIDEWAYS controls, debate eligibility,
  confidence policy, liquidity thresholds, regime resolver, and position sizing;
- canonical actionability, Top-3 eligibility, or the number of BUY signals;
- C1–C8 challenger promotion. Outcome-changing challengers remain subject to
  `SHADOW_MODE_PROTOCOL.md`; no shadow result has been treated as production
  evidence.

## Decision

Do **not** loosen the system to make more tickers pass. Preserve the current execution contract and redesign the information contract around it.

The target is a **selective recommendation system** with two explicitly separate layers:

1. **Actionability layer:** deterministic `PASS / REJECT / ABSTAIN` based on data integrity, setup geometry, execution risk, and portfolio controls.
2. **Recommendation-information layer:** calibrated probability/expected utility when validated, interval or prediction set, exact blocker and distance, evidence quality, and the observable event that would change the status.

A probability, model vote, factor score, or near-miss rank must never override a failed hard gate. Success is lower decision error and better calibration at a given coverage—not a higher BUY count.

## Why this direction is evidence-led

The current July baseline screened roughly `961 → 808 → 461 → 4 → 2`; liquidity/ADT, relative strength, and EMA20 were the largest losses. Both final candidates were still non-executable. Low pass-through alone is not proof that thresholds are wrong.

Current artifacts already contain the safer semantic split—`NO_TRADE`, model opinion `HOLD`, preflight source, and exact setup rejection—but human reports collapse useful distinctions. MYOR's hypothetical R/R was 1.50 against a 2.000 floor, while MAPA and AKRA were 0.64 and 0.36; all appeared as the same empty watchlist. Two fixed replay packets added 11 non-executable cases (6 `RR_TOO_LOW`, 4 `NO_MOMENTUM`, 1 `INSUFFICIENT_DATA`) and still produced no populated watchlist.

That is direct evidence for better explanations and graded near-miss states. It is **not** evidence for lowering 2.0 to 1.5.

Historic reject rates are also contaminated as calibration evidence: prior code downgraded HOLD consensus to AVOID in 124/272 debates, forced the bear to vote bearish in 378/378 records, and once let the higher-confidence bear win deadlocks. Historic “rejection frequency” therefore mixes market conditions with repaired software bias.

## Proposed recommendation contract

Every candidate should end with one canonical record. Fields marked “shadow” are invisible to live actionability until the protocol passes.

```text
candidate_identity
  ticker, snapshot_id, snapshot_hash, as_of, source_vintages

screening_state
  quant_rank, score, universe_count, gates_evaluated

model_opinion
  rating, raw_confidence, effective_confidence, agents_invoked, rounds

policy_decision
  EXECUTABLE_BUY | NO_TRADE | ABSTAIN
  decision_source, sizing_allowed

recommendation_state
  QUALIFIED | WAIT_TRIGGER | NEAR_MISS | SINGLE_GATE_REJECT |
  HARD_REJECT | DATA_INSUFFICIENT

blockers[]
  gate_id, hard_or_soft, observed, threshold, unit,
  absolute_gap, percentage_gap, provenance, next_observable_trigger

hypothetical_setup
  entry, target, stop, rr, required_rr
  explicitly_non_executable: true

calibration (shadow until promoted)
  p_target, p_stop, p_timeout_or_exception,
  conditional_timeout_return or directly_modeled_expected_net_R,
  expected_net_R, interval_or_prediction_set,
  cohort_n, calibration_window, Brier, ECE, model_version,
  distribution_shift_flag

evidence_quality
  complete | degraded | stale | missing, with per-source age
```

### Recommendation-state semantics

| State | Meaning | May size? | May receive opportunity rank? |
|---|---|---:|---:|
| `QUALIFIED` | All actionability gates passed | Yes, subject to portfolio controls | Yes |
| `WAIT_TRIGGER` | Valid geometry, but price/pullback/confirmation trigger not met | No | Yes, within a separate waitlist |
| `NEAR_MISS` | Exactly one measurable/reversible setup gate failed, all integrity gates passed, and normalized shortfall is within a **predeclared presentation-only band of 10%** | No | Yes, within a separate near-miss list |
| `SINGLE_GATE_REJECT` | Exactly one setup gate failed but is outside the near-miss band; e.g. R/R 1.50 vs 2.00 is a 25% shortfall, not “near” | No | No opportunity rank; exact distance and trigger only |
| `HARD_REJECT` | Invalid/stale provenance, impossible geometry, market constraint, or multiple safety failures | No | No tradable rank; severity/explanation only |
| `DATA_INSUFFICIENT` | Required evidence is absent or unmeasurable | No | No opportunity rank |

The 10% band is a UX classification policy, not a relaxed gate; it may be narrowed but not widened after outcomes are seen. Gates without a meaningful monotone normalized distance default to `SINGLE_GATE_REJECT`. Near-miss ranking must be deterministic and non-promotional. Suggested tuple: `(normalized_shortfall, evidence_completeness, quant_rank)`. It must never mix with executable ranking.

## Gate audit

Categories are deliberately conservative:

- **KEEP AS-IS:** current behavior is an integrity/regulatory invariant or evidence supports retaining it.
- **ADD EXPRESSIVENESS:** retain behavior; expose the observed value, threshold/provenance, reason, and next trigger.
- **CHANGE:** evidence supports different logic. Every CHANGE below is shadow-gated; none is authorized live.

### Data provenance and universe gates

| Gate | Current owner and behavior | Evidence for current threshold | Category | Risk / reversibility |
|---|---|---|---|---|
| XLSX freshness | `core/quant_filter/config.py:10-55`; >3 calendar days degrades score by 10, >5 aborts | No external alpha calibration found; direction is safely conservative and stale inputs are a known provenance hazard | **ADD EXPRESSIVENESS** — show age, cutoff, score impact, source hash | Low-risk display change; threshold change could alter coverage and needs shadow |
| Snapshot identity/hash/ticker match | `core/orchestrator/legacy.py:3110-3153`; fail closed | Reproducibility and point-in-time integrity require immutable identity; mutable `latest_*` already broke historical provenance | **KEEP AS-IS** | Removing it would create silent look-ahead/misattribution; trivially reversible but unacceptable |
| Candidate schema | `core/candidate_intake.py:18-69`; ticker and numeric price required | An action cannot be priced without identity/price | **KEEP AS-IS** | No safe relaxation |
| Special-monitoring exclusion | `config.py:121`; exclude `PEMANTAUAN KHUSUS` | Current official-rule refresh was outside the verified source set; internal safety rationale is plausible but exact scope is not independently calibrated here | **ADD EXPRESSIVENESS** — report status/source/date | Relaxation could expose special-risk securities; reversible config, high loss risk |
| Minimum close price | `config.py:97`; strictly >Rp100 | No fresh empirical evidence supports exactly Rp100 | **ADD EXPRESSIVENESS** | Do not lower from pass-rate pressure; shadow any alternative |
| Sector DER caps | `config.py:99-113`; 1.0–8.0 by sector | Sector differentiation is sound; exact caps lack prospective return calibration | **ADD EXPRESSIVENESS** | Moderate coverage/model-risk; reversible |
| PBV hard/sector caps | `config.py:114-115`; PBV<6 and below sector 80th percentile | No current causal validation; valuation alone is not execution eligibility | **ADD EXPRESSIVENESS** | Could suppress momentum names; do not change without paired shadow |

### Quant technical and fundamental gates

| Gate | Current owner and behavior | Evidence for current threshold | Category | Risk / reversibility |
|---|---|---|---|---|
| Suspension/FCA/zero volume | `core/quant_filter/pipeline.py:736-769`; >3 zero-volume days or severe recent anomaly rejects | Direct tradability/liquidity integrity | **KEEP AS-IS**, add raw volume diagnostics | Relaxation risks unfillable recommendations |
| Momentum EMA20 | `pipeline.py:812-842`; price≥EMA20, or ≥.97× in DEFENSIVE/HIGH/BEAR_STRESS | Internal funnel says 63 names fail; no prospective evidence that failure is excessive | **ADD EXPRESSIVENESS** — observed ratio and regime-specific floor | Lowering increases coverage, not demonstrated quality |
| Relative strength vs IHSG | `pipeline.py:844-857`; 1m stock return must match/beat IHSG | 109 July losses, but count is not causal evidence; no fresh study justifies a new number | **ADD EXPRESSIVENESS** | Regime-sensitive; reversible after shadow |
| Mean-reversion eligibility | `pipeline.py:804-879`; below EMA20, ≥.80×MA200, RSI≤45, 1m return≥−30% | Separate strategy semantics are appropriate; exact numbers not externally validated here | **ADD EXPRESSIVENESS** | Mixing it with momentum would confound attribution |
| Ex-date | `utils/exdate_scanner.py:32-33`; ≤7 days rejects, 8–30 warns | Corporate-action gap risk is real; 7/30 are project policy, not verified universal constants | **ADD EXPRESSIVENESS** — show event/date/days | Relaxation has event risk; reversible |
| RSI ceiling | `config.py:175`, `pipeline.py:880-885`; RSI>70 rejects | Internal signal study did not validate RSI alpha but also did not justify removal; only two July funnel losses | **ADD EXPRESSIVENESS** | Changing could add crowded entries; shadow only |
| ADT screen | `config.py:160`, `pipeline.py:920-925`; ≥Rp10B | Largest July loss (237); high count is not evidence against fill-risk control | **ADD EXPRESSIVENESS** — ADT, intended size, participation rate | Threshold must be capacity-derived in shadow, not pass-rate-derived |
| ATR/price | `config.py:161`, `pipeline.py:895-930`; classic ATR≤5% | Code proves GARCH does not own inclusion; old GARCH blame is false | **ADD EXPRESSIVENESS** — identify classic ATR source | Lowering/widening changes stop/turnover risk |
| Volume confirmation | `config.py:171`, `pipeline.py:936-940`; surge ratio≥1.0 | Internal IC did not show volume alpha, but liquidity/confirmation role differs from score alpha | **ADD EXPRESSIVENESS** | Any removal must isolate fill and timing effects |
| Fundamental triple fail | `pipeline.py:1021-1090`; Piotroski<4, Altman<1.1, ROE<10 each penalize; all three hard-reject | Individual score evidence is weak; combined distress guard is conservative and interpretable | **ADD EXPRESSIVENESS** — list each component and penalty | Relaxation could admit distressed names; shadow only |
| Composite score floor | `config.py:287-293`, `pipeline.py:1984-2028`; 45 DEFENSIVE, 35 otherwise, then top 10 | No July signal passed stated HLZ/FDR validation; therefore current weights/floors are not promotion-grade, but there is no evidence for a better floor | **ADD EXPRESSIVENESS** | Changing score and gate together destroys attribution |
| OCF/Price/RNOA score influence | `config.py:209-224`, `pipeline.py:1239-1351` | Actual IDX4 paper supports characteristics; repo is not the factor model and internal OCF coverage is zero | **CHANGE C5** — label current features accurately; factor challenger only in shadow | High model/data risk; feature flag and full revert |

### Pre-debate trade setup gates

| Gate | Current owner and behavior | Evidence for current threshold | Category | Risk / reversibility |
|---|---|---|---|---|
| Critical-risk text / critical ex-date / DEFENSIVE below MA200 | `legacy.py:3014-3036,3239-3291`; terminal no-trade | Fail-safe pre-CIO design; note the live MA200 gate is DEFENSIVE-only, not HIGH as older notes claim | **ADD EXPRESSIVENESS** | Low display risk; behavior change needs shadow |
| OHLCV integrity | `services/trade_setup.py:92-137`; H/L/C/V required, volume cannot be all zero | Technical indicators are undefined without valid inputs | **KEEP AS-IS** | No safe relaxation |
| History completeness | `trade_setup.py:35-38,262-298`; ≥60 bars for short indicators and ≥250 for MA200 execution | Indicator lookback logically requires data; 30-signal convention is unrelated | **ADD EXPRESSIVENESS** — observed/required bars and listing age | Shortening changes estimator stability |
| Preflight noise | `services/debate_chamber.py:2477-2526`; gap<1 ATR rejects, 1–1.5 conditional, ≥1.5 clean | Geometry protects against stops inside ordinary noise; exact 1/1.5 values have no fresh prospective validation | **ADD EXPRESSIVENESS** | Loosening increases stop-out risk |
| Momentum breakdown/confirmation | `debate_chamber.py:4070-4111`; breakdown at 5d≤−3% and below EMA20; other negative-5d setups require EMA20, positive 1d, volume≥1 | Current confirmed recalibration is already shadow-only; no mature outcome evidence exists | **ADD EXPRESSIVENESS** — show each failed subcondition; keep shadow-only | Promotion without outcomes is high risk |
| Entry-range position | `trade_setup.py:364-373`; above=wait pullback, below=no momentum, inside=executable | Correctly separates valid setup from current entry timing | **ADD EXPRESSIVENESS** — exact trigger price | Low risk, fully reversible |
| Debate eligibility | `trade_setup.py:325-396`; only `EXECUTABLE` reaches LLM | Saves cost and prevents prose from overriding invalid geometry | **ADD EXPRESSIVENESS** — zero-agent explanation and hypothetical setup | Relaxing allows LLM persuasion around hard math; high risk |
| R/R floor | `utils/trade_math.py:19-41,253-289`; `max(2.0, tier×regime multiplier)` | No fresh evidence justifies a lower number. Current effective floor is 2.0 except default-tier DEFENSIVE/BEAR_STRESS/UNKNOWN=2.106 | **ADD EXPRESSIVENESS**, **no numeric change** | Lowering directly degrades payoff asymmetry; reversible but costly to learn live |
| SIDEWAYS controls | `core/idx_market_params.py:84-93`; `trade_math.py:30-41`; `utils/technicals.py:23-35` | There is no single multiplier: confidence×.85, consensus floor .70, R/R×1.2, stop 2.5×ATR | **ADD EXPRESSIVENESS** — display all four | Changing one has interactions; factorial shadow required |
| Envelope geometry | `debate_chamber.py:4113-4295`; stop≤10%, stop distance≥1 ATR, target/resistance and sector caps, R/R at entry high | Prevents impossible/highly optimistic setups; exact caps lack prospective causal evidence | **ADD EXPRESSIVENESS** | Relaxing multiple fields simultaneously makes attribution impossible |

### Debate, CIO, and evidence gates

| Gate | Current owner and behavior | Evidence for current threshold | Category | Risk / reversibility |
|---|---|---|---|---|
| UNKNOWN/DEFENSIVE regime policy | `core/idx_market_params.py:70-115`; UNKNOWN blocks, BEAR/DEFENSIVE restricts; `debate_chamber.py:4705-4720` clamps BUY to HOLD≤.55 | Current market has conflicting horizons; abstention under uncertainty matches selective-prediction research | **ADD EXPRESSIVENESS** — raw opinion, policy outcome, posterior/horizon | Loosening risks false bull classification |
| HMM regime model | `core/regime_hmm.py:83-388`, `core/regime_gate.py:56-183`; three states, partial features | Papers exist but are not IDX validation; one finds a jump model more persistent than HMM | **CHANGE C4a** — one stability/persistence challenger in shadow; no live swap | High interaction risk; dual-run and instant resolver revert |
| Foreign-flow input | `core/regime_gate.py:145-152`; runtime omits a feature the model supports | Missing feature cannot look like full-model evidence | **CHANGE C4b1** — point-in-time input under its own protocol ID | Source/leakage risk; instant omission revert |
| MSCI review state | `core/idx_market_params.py:66-68`; hard-coded active | Official MSCI status is conditional and date-bound | **CHANGE C4b2** — official-source dated/expiring record under a separate protocol ID | Low technical reversibility; potential regime discontinuity |
| Consensus | `debate_chamber.py:814-851,1650-1790,3596-3666`; 75%/≥3 votes, max 3 rounds, regime confidence floors | Historic forced-bear/deadlock contamination prevents retrospective calibration; no evidence supports lowering | **ADD EXPRESSIVENESS** — vote denominator, abstentions, effective confidences | Threshold change would alter signal volume and require clean prospective test |
| Fundamental-quality veto | `debate_chamber.py:3596-3666`; FAIL vetoes BUY to HOLD | Safety rationale; no clean prospective uplift estimate | **ADD EXPRESSIVENESS** | Removing could admit low-quality narratives |
| CIO bullishness limit | `debate_chamber.py:4506-4703`; cannot exceed voting winner; conditional AVOID→HOLD only | Prevents a final prose node from escaping ensemble evidence | **KEEP AS-IS**, expose override chain | Relaxation creates unbounded override risk |
| Unverified fair value | `debate_chamber.py:595-651`; current-run unverified FV nulled | Provenance correctness is more important than apparent coverage | **KEEP AS-IS** | No safe relaxation |
| Citation guard | `debate_chamber.py:5228-5282`; advisory | Current advisory status cannot support a hard trust claim | **ADD EXPRESSIVENESS** — verified/unverified source counts | Hard-gating could reduce coverage; shadow first |
| Evidence staleness | `debate_chamber.py:5197-5224`; >24h confidence penalty up to 30% | Direction is correct; exact curve lacks prospective calibration | **ADD EXPRESSIVENESS** | Change affects confidence calibration |
| Breaking-news adjustment | `debate_chamber.py:451-469`; −.10/−.20 negative, +.05/+.10 positive | Fixed increments are policy heuristics, not calibrated probabilities | **ADD EXPRESSIVENESS** — show raw and adjusted confidence | Any numeric change belongs in probability calibration shadow |
| IndoBERT prior | `services/indobert_sentiment.py`, `debate_chamber.py:3016-3058`; optional prompt prior, enabled in the audited local `.env` despite the code default of `False` | Exact checkpoint is general-domain and raw softmax is uncalibrated for IDX finance | **CHANGE C3** — domain benchmark/calibration before influence | Feature flag/off switch; risk of confident domain error |
| Schema minimum gain/RR and wait | `schemas/debate.py:419-611`; BUY→HOLD if gain<3% or R/R<1; confidence<.60 sets wait | Secondary schema safety floor is dominated by later canonical R/R; retaining avoids malformed outputs | **ADD EXPRESSIVENESS** — identify which layer fired | Consolidation should preserve stricter canonical outcome |
| Post-CIO low confidence/coherence | `legacy.py:3694-3737,3896-3985`; <25% becomes insufficient; geometry and canonical RR checked | Fail-safe consistency; exact 25% is not calibrated | **ADD EXPRESSIVENESS** | Lowering lets unusable model outputs proceed |

### Risk governor, portfolio, and reporting gates

| Gate | Current owner and behavior | Evidence for current threshold | Category | Risk / reversibility |
|---|---|---|---|---|
| Rating and confidence | `core/risk_governor.py:27-56,651-673`; AVOID/SELL or confidence<.60 reject | Historic confidence is not calibrated; .60 should be treated as policy, not a 60% empirical success rate | **ADD EXPRESSIVENESS** plus **CHANGE C1** for shadow calibrated probability | Do not replace policy with raw model confidence |
| Overvaluation | `risk_governor.py:674-690`; reject unless `momentum_play` | FV is model-dependent; exemption's claimed protections are not implemented and have no fresh outcome support | **CHANGE C8** — remove/disable the exemption and fail closed | Reachable via the CIO prompt/schema; sampled artifacts mostly show `false`, but prevalence is not proof of dormancy. Replay verifies no unintended behavior; enabling any exemption is out of scope |
| Canonical/implausible R/R | `risk_governor.py:692-718`; recompute, minimum floor, reject ≥5, countertrend floor 2.5 | Recompute precedence prevents stale/LLM ratios; exact 5/2.5 lack prospective calibration | **ADD EXPRESSIVENESS**, no numeric change | Relaxation can promote impossible or countertrend setups |
| ARA/ARB/T+2/target reach | `risk_governor.py:720-750`; hard/soft execution practicality flags | Exchange mechanics are real; exact soft policy must retain source dates | **ADD EXPRESSIVENESS** | Rule drift requires official refresh, not ad hoc change |
| Liquidity | `risk_governor.py:58-61,121-438`; <Rp2B reject, Rp2–10B conditional, missing data currently fails open | Selective-prediction safety favors abstention when a required capacity input is unknown; upstream ≥Rp10B does not protect bypass paths | **CHANGE C7** — missing liquidity becomes `ABSTAIN` before sizing | May reduce coverage; highly reversible flag; safer failure direction |
| Price state and DEFENSIVE clamp | risk governor: inside entry deployable, above wait, below watch; DEFENSIVE turns deployable into watchlist | Preserves timing and regime safety | **ADD EXPRESSIVENESS** | No evidence for loosening |
| Daily circuit breaker | `risk_governor.py:63-98`; realized daily loss≥3% halts sizing | Portfolio survival invariant; exact 3% not re-estimated here | **KEEP AS-IS** | Change is high risk and outside signal-calibration scope |
| Portfolio heat / kill switch | `core/portfolio_guard.py:22-35,101-148`; heat 1.3%, 30d average PnL<−15% | Portfolio-level containment | **KEEP AS-IS** | High consequence; separate risk mandate required |
| Position sizing | `core/quant_filter/position_sizer.py:334-520`; BUY-only, valid geometry, lot/max loss/deployment/regime caps | Prevents recommendation from becoming an unbounded allocation | **KEEP AS-IS**, add binding-constraint explanation | No scope to loosen in this audit |
| Ranking | `legacy.py:5705-5916`; deployable/sizing allowed; confidence/RR score; top 3 | Score uses uncalibrated confidence and tent-shaped R/R; eligibility remains separate | **ADD EXPRESSIVENESS**; calibrated challenger under C1 | Ranking changes need paired outcome comparison |
| “Minimum conviction” fallback | `core/portfolio_optimizer.py:72-137`; if none clear .30, restores all scorable candidates | It is a preference, not a hard gate | **ADD EXPRESSIVENESS** — rename/report fallback | Making it hard would change coverage without evidence |
| Report compression | `services/report_formatter.py:614-630,1797,1843-1939`; generic labels hide retained reason/geometry | Live artifacts already preserve reason codes and hypothetical envelope | **ADD EXPRESSIVENESS — highest-priority** | Display-only, low risk, fully reversible |

## Outcome-changing candidates

These are the only Phase-3 items labeled CHANGE. None is approved for live use.

### C1 — Calibrated selective-recommendation challenger

Fit competing-risk probabilities for target-first, stop-first, and timeout/exception outcomes within the frozen 15-day primary horizon, plus conditional timeout return or a directly modeled expected net R, using **closed, point-in-time outcomes**. Compare held-out calibration methods (intercept/slope baseline, isotonic only with enough data, temperature/Platt where structurally appropriate). Add risk–coverage curves and, only when assumptions are credible, a conformal prediction/risk set.

The live actionability gate remains authoritative. Until validation, the field is explicitly `SHADOW_UNCALIBRATED` and must not alter rating, rank, or sizing.

### C2 — Discount-rate decomposition and provenance

Replace the conceptual `SBN + beta × total ERP` challenger with a **pre-registered primary rating path**: latest point-in-time 10-year IDR government yield minus the latest point-in-time Damodaran rating default-spread estimate, plus the unchanged control beta × mature ERP, plus `lambda=1` × rating CRP. Keep the existing sector equity premium unchanged in the first experiment so only sovereign-risk construction changes. Currency/tenor conventions must match; each source uses its own predeclared expiry (daily yield: two business days; published country-risk workbook: its stated next-update date, capped at 190 calendar days). Future releases are prohibited; an expired source makes the challenger abstain.

The CDS construction is a separately registered sensitivity/trial, never selected after outcomes are seen. Beta/lambda re-estimation is also a future, separate challenger. This may change fair value and indirectly eligibility, so source authority alone cannot ship it.

### C3 — Finance-domain sentiment calibration

Stop treating a general-domain checkpoint's raw softmax as calibrated financial authority. Build a time-split, ticker/context-aware finance benchmark; compare the current checkpoint, a finance-tuned challenger, keyword/LLM baselines, and an abstain class. Any impact on debate confidence remains shadow-only.

### C4a — Regime-model stability

Run the current HMM alongside exactly one frozen persistence-penalized jump/regime challenger; report posterior entropy, transition stability, turnover, and horizon conflict. State labels are aligned using training data only. The conservative execution resolver stays the control.

### C4b1 — Point-in-time foreign-flow input

In a separate protocol ID, test point-in-time foreign-flow wiring with explicit as-of/source lineage. Missing/expired flow must become UNKNOWN or leave the conservative control unchanged, never make the system more permissive.

### C4b2 — Dated MSCI review state

Replace the hard-coded MSCI Boolean with an official-source, dated, expiring record under its own protocol ID. Do not combine C4a, C4b1, or C4b2: otherwise model logic, foreign-flow data, and index-provider state are causally inseparable.

### C5 — Actual IDX4 factor challenger

Immediately correct documentation to “IDX4-inspired characteristics.” A behavioral challenger may be built only after point-in-time OCF and EBIT/BEV coverage supports factor portfolios, factor returns, and out-of-sample exposures matching the paper's definitions. Do not infer factor validation from stock-level tier weights.

### C6 — DSR/trial-governance correction

Reconcile the duplicate DSR implementations, register every tried strategy/configuration, estimate effective independent trials, and validate autocorrelation handling before DSR/PBO can be used in promotion decisions. C6 passage is a prerequisite for every DSR-based GO. `n_trials=1` must be labeled PSR-like rather than advertised as selection-deflated evidence. The `.95` bar is a predeclared project policy, not a theorem.

### C7 — Missing-liquidity abstention

Any downstream path that can size a candidate must return `ABSTAIN / DATA_INSUFFICIENT` when ADT/capacity is unavailable. This closes a bypass-path asymmetry while leaving measured Rp2B/Rp10B thresholds unchanged.

### C8 — Remove/disable the reachable `momentum_play` exemption

The risk-governor comment claims protections that production code does not enforce. The CIO prompt explicitly instructs the model to set `momentum_play=true` for a FAIL/PASS volume-breakout case, the schema accepts it, and the governor honors it. Sampled artifacts currently show mostly `false`, but that is not evidence that the branch is unreachable. Treat it as a reachable exemption until prevalence is measured from immutable artifacts. Remove/disable the exemption and fail closed. A future proposal to enable an exception is explicitly out of scope and would require new research, a new CHANGE item, and separate approval; R/R 2.5 and half-size comments are not evidence.

## Post-approval sequence and status

1. **Implemented — display-only expressiveness:** conditional methodology,
   reason-code rendering, exact gate distance, non-executable hypothetical
   geometry, and separate wait/near-miss/reject/abstain states.
2. **Implemented — provenance language:** current factor features are labeled
   “IDX4-inspired”; probability calibration remains explicitly unavailable.
3. **Not promoted — shadow challengers:** immutable paired control/challenger
   records for C1–C3, C4a, C4b1, C4b2, and C5–C8 remain governed by the shadow
   protocol and require component-specific implementation/data readiness.
4. **Pending real outcomes:** no calendar-only promotion and no threshold
   changes.
5. **Still approval-gated:** promote at most one causally attributable component
   at a time after its predeclared GO gate.

## Explicit non-recommendations

- Do not lower the 2.0 R/R floor to turn MYOR's 1.50 into a pass.
- Do not remove EMA20, relative strength, ADT, ATR, RSI, or volume gates because the funnel is narrow.
- Do not call a short IHSG rebound a bull regime while 3–6 month/YTD evidence remains deeply negative.
- Do not hard-code July GARCH coefficients.
- Do not claim the current stock-level scoring is the published IDX4 model.
- Do not use raw IndoBERT confidence or LLM confidence as a calibrated probability.
- Do not promote from a shadow report with zero mature outcomes.
- Do not evaluate a challenger without the current defensive system running on the identical opportunity set.

## Success criterion

The redesign succeeds if, at the same or lower realized risk, users can answer:

- Why was this candidate rejected?
- How far was it from each gate?
- Is the failure hard, data-driven abstention, or a reversible wait condition?
- What evidence would change the status?
- How reliable was this probability in comparable, point-in-time cases?

“More BUYs” is neither a metric nor a GO condition.
