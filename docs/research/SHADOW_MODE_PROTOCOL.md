# Shadow Mode / Paper-Trading Protocol

**Status:** protocol design plus isolated RS-P2-008…013 engine/evidence foundation; no collection or live integration; do not run without separate approval  
**Control:** current defensive system, frozen by full content manifest at protocol start  
**Challengers:** Phase-3 CHANGE items C1–C3, C4a, C4b1, C4b2, and C5–C8 from [the redesign proposal](DEFENSIVE_TO_RECOMMENDATION_REDESIGN_2026-07.md)

**Execution handoff:** [Master Implementation Checklist](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md)

## Purpose and non-negotiable rule

This protocol decides whether a proposed component deserves production trust. It is not a mechanism for manufacturing more BUY signals.

The current defensive system and every challenger must process the **same point-in-time opportunity set, data vintages, timestamps, costs, and outcome labels in parallel**. A challenger is never evaluated in isolation, and a probability or paper-trading result never overrides the control during shadow mode.

## Pre-registration before the first observation

Create and sign an immutable protocol manifest before collecting results. It must contain:

- protocol ID, start time, owner, reviewer, and approval reference;
- full file-content hashes for the control and each challenger—not only Git HEAD, because the audited worktree is dirty;
- exact candidate universe, quant mode, trading calendar, **15-trading-day primary horizon**, and descriptive 3/5/10-day secondary checkpoints;
- all thresholds, model hyperparameters, feature definitions, label definitions, source URLs, and expiry rules;
- fee, tax, bid–ask/slippage, lot, liquidity, and corporate-action treatment;
- every model/configuration tried, including discarded variants, in the DSR/PBO trial registry;
- component-specific GO/NO-GO criteria copied verbatim from this document;
- rollback owner and production feature flag for any later approved promotion.

Any material post-start change creates a **new protocol ID and new trial**. It may not overwrite the original run.

## Frozen chronological validation design

Any learned/calibrated challenger uses an anchored walk-forward design with three non-overlapping roles:

1. **Training:** oldest point-in-time segment; all feature/model fitting occurs here.
2. **Calibration/selection:** later segment; calibration mapping, hyperparameters, and the one frozen challenger are chosen here. Every tried variant enters the DSR/PBO and hypothesis-family registry.
3. **Untouched prospective test:** newest segment; no fitting, threshold tuning, state relabeling, annotation-rule change, or variant selection may use its outcomes.

Apply a 15-trading-day purge/embargo gap at every train/calibration/test boundary, and purge any issuer/event whose label window crosses a boundary. Nested tuning uses only inner chronological splits. The test cohort remains untouched until its fixed terminal date and maturity. Monthly reports are operational/blinded and cannot trigger GO; any early efficacy stop requires a pre-registered always-valid e-value/alpha-spending rule. Safety violations may stop immediately without an efficacy test.

## Experimental unit and independence

### Raw event

A raw event is one ticker × signal timestamp × frozen snapshot. Both control and challenger receive it, even if one abstains.

### Order and outcome labels

The primary estimand is `target before stop within 15 trading days after a valid fill`. The 3/5/10-day values are separate secondary labels that mature independently; day 3 never closes the 15-day label. Only the 15-day result drives the primary GO test. Secondary-horizon hypothesis tests use the registered multiplicity correction.

The manifest predeclares order activation (never the signal bar), validity/expiry, limit/range handling, slippage, and corporate-action convention. A buy that opens above the maximum permitted entry is unfilled; a marketable limit that opens at or below its limit fills at the observed open plus costs, capped by the limit; an intraday touch fills at the limit plus admissible costs. A gap through a stop exits at the adverse open, not the stale stop price. If the entry itself gaps through the planned stop, record a same-open fill and stop rather than censoring the adverse event; use the frozen entry-high risk basis for net-R. A favorable gap through a target uses the observed open under the same cost rule. If daily OHLC touches both target and stop without an intraday sequence, score stop first and flag ambiguity. If an intraday entry and target are both touched but their order is unknowable, do not credit the target and persist the ambiguity through the final outcome.

The RS-P2 engine accepts **raw-as-traded bars only**. Split events rescale price geometry and position quantity on the same frozen trading session. Dividends follow the frozen total-return or price-return convention and require ownership before the ex-date. Publication and applicability use separate clocks: an event belongs to signal-time evidence when it was published by the signal, but affects a position only on its effective trading session. Rights issues currently close the affected label as `INVALID / RIGHTS_POLICY_UNSUPPORTED`; the engine does not assume automatic exercise. Supporting rights economically requires a new pre-registered rule for election, subscription, delivery/lapse, costs, and monetary-risk basis.

The current isolated evaluator uses one frozen exchange lot, source-supplied prices with no engine-side tick rounding, and no liquidity model. Entry costs apply buy commission plus one slippage and bid-ask charge to entry notional; exit costs apply sell commission, sell tax, plus one slippage and bid-ask charge to exit notional. These limits must be replaced or explicitly retained in a new manifest before a fixed-notional or portfolio cohort starts.

For each horizon, the label closes at target, stop, that horizon's maturity, or its predeclared exceptional event. Unfilled orders are a separate outcome and never backfilled with future knowledge.

### Independent signal cluster

The minimum sample is counted in **independent closed clusters**, not raw rows. Issuer/economic groups, correlation clusters, and calendar/systemic-event blocks are frozen from pre-run data. Treat events as one cluster when any apply:

- same ticker with overlapping 15-trading-day outcome windows;
- same issuer/economic group or predeclared highly correlated cluster with overlapping windows;
- duplicated/reissued recommendation from the same underlying setup;
- same predeclared calendar/systemic-event block or broad same-date market shock.

The cluster representative and aggregation rule are predeclared; a bootstrap does not discover independence after the fact. Report raw `n`, dependence-adjusted effective `n`, unique issuers, unique signal dates, and unique event blocks. Use issuer/group clustering plus date-block or two-way resampling. Thirty counts only after this dependence adjustment and a metric-specific power/precision calculation.

### Minimum is a floor, not sufficiency

Every outcome-changing component requires **at least 30 independent closed clusters**, matching the existing project floor for agent-weight fitting. Research did not support 30 as a universal statistical sufficiency rule. Promotion also requires the component-specific precision, calibration, DSR, drawdown, and regime criteria below.

If 30 closes but confidence intervals remain too wide, the decision is **CONTINUE / NO-GO**, never automatic GO.

## Honest duration

With a 3–15 trading-day horizon, the last observation needs roughly three additional calendar weeks to mature. At only 2–4 independent qualifying clusters per week:

- 30 clusters require about 8–15 collection weeks plus maturation: **roughly 2.5–4.5+ months**;
- 60 clusters require roughly **4–8+ months**;
- 100 clusters require roughly **6–12+ months**;
- sparse missing-liquidity/input cohorts may require **12–18+ months**; full three-state regime and monthly-factor validation can require **2–5+ years**.

Components may collect shadow observations in parallel. They must be promoted **one at a time** so the next control includes only the previously approved change. Sequentially validating ten sparse components can take years; that is preferable to confounded production learning.

## Control and paper portfolios

For every timestamp, persist:

- the complete raw candidate set before pruning;
- control and challenger gate decisions, reason codes, exact observed values and thresholds;
- entry/target/stop/R/R and any calibrated probability;
- control/challenger ranks and position size under the state appropriate to the estimand below;
- data/source vintages, hashes, regime features/posteriors, and model versions;
- realized label, costs, fill assumptions, return, and R multiple after maturity.

Run three paired views:

1. **Decision view:** candidate-level paired differences using the frozen control portfolio state, including cases where only one side acts.
2. **Fixed-notional view:** identical notional and frozen control state for both sides, isolating signal quality.
3. **Policy portfolio view:** control and challenger evolve independently/path-dependently from identical starting capital and identical risk/cost rules.

Portfolio drawdown, volatility, DSR inputs, exposure, and turnover come from a daily marked-to-market NAV series, never from a sequence of closed-trade rows.

The live system continues to follow the control only. Shadow orders are records, not exchange orders.

## Common metrics

All component reports must include these metrics even when a value is undefined. Undefined is reported as `NOT_ESTIMABLE`, never coerced to zero.

### Return and risk

- net return and net R per opportunity, acted signal, and independent cluster;
- target-before-stop hit rate with Wilson interval and issuer/date-block interval;
- planned-versus-executed entry/stop/target, fill rate, gap/slippage, and execution deviation;
- competing-risk calibration for `p_target`, `p_stop`, and `p_timeout_or_exception`, including conditional timeout return; expected net R is modeled directly or computed as the sum of each outcome probability × its conditional net R, then compared with realized net R by bias, MAE, slope/intercept, and bins;
- chronological maximum drawdown, downside deviation, turnover, exposure, and transaction costs;
- paired challenger-minus-control return and R with issuer/date-block confidence interval;
- Deflated Sharpe Ratio, **only after C6 passes**, using the registered effective number of tried configurations, skew, kurtosis, and the C6-validated autocorrelation-adjusted effective sample size/Sharpe variance; a fixed-origin non-overlapping 15-day return series is reported as sensitivity;
- Probability of Backtest Overfitting/CSCV when the number of configurations permits it.

Raw annualized Sharpe is descriptive only. Per-trade observations must not be annualized as if overlapping positions were independent.

### Probability calibration

- Brier score and Brier skill versus a time/regime base-rate model;
- log loss;
- Expected Calibration Error with five predeclared equal-frequency test bins (minimum 20 observations per bin) plus adaptive-bin sensitivity;
- logistic calibration intercept (log-odds scale) and slope, with confidence intervals;
- reliability diagram by the primary horizon and only predeclared, adequately powered regime/liquidity/market-cap/recommendation slices;
- selective risk versus coverage curve;
- interval/prediction-set coverage and average width, if conformal output is used;
- distribution-shift tests and out-of-domain rate.

### System behavior

- coverage, abstention, executable count, near-miss count, and hard-reject count;
- false-promotion count: challenger executable while a non-overridable integrity gate failed;
- data completeness, source age, and hash/provenance failure rate;
- latency/cost, LLM calls, and retry/failure rate;
- reason-code agreement and human-review disagreement.

Signal frequency is reported diagnostically and is never a GO target.

## GO / NO-GO framework

### Universal gate for every component

1. **Integrity:** 100% immutable snapshot lineage; zero detected look-ahead; zero control/challenger opportunity-set mismatch.
2. **Sample:** at least 30 dependence-adjusted independent affected clusters/fixtures; unaffected rows do not pad `n`. Component-specific power/precision rules can require more.
3. **Regime:** at least two predeclared strata for regime-sensitive signal components; C4a has a stricter all-state rule.
4. **Safety:** zero false promotions across snapshot integrity, impossible geometry, critical ex-date, suspension, circuit breaker, and missing mandatory data.
5. **Multiplicity/stopping:** all variants enter the registry; 15 days is the sole primary horizon; simultaneous secondary tests use Holm family-wise correction; the terminal test date is fixed. No post-hoc subset, regime, cost, or horizon becomes primary.
6. **Independent review:** a reviewer reproduces the result from the immutable manifest and signs before any promotion request.

### Outcome-changing model gate — C1, C2, C3, C4a, C4b1, C4b2, C5

In addition to the universal gate:

- **C6 must already have passed** for the frozen DSR implementation, including autocorrelated-return fixtures. Until then every DSR-based component decision is CONTINUE/NO-GO;
- report control DSR, challenger DSR, and DSR of the paired daily marked-to-market return difference with the complete effective-trial registry;
- for a **superiority** claim, the one-sided 95% issuer/date-block confidence lower bound for mean incremental net R must be `>0` and challenger/paired-difference DSR must each be ≥.95;
- for a predeclared **non-inferiority + other benefit** claim, challenger DSR must be ≥.95, paired-difference DSR is reported but need not pass .95, the incremental-net-R lower bound must exceed `−0.05 R` per independent cluster (a project risk-budget policy, not a literature constant), and the designated calibration/stability metric must strictly improve;
- challenger maximum drawdown may not be worse than control by more than the greater of 1 percentage point or 10% relative;
- probability output additionally requires positive Brier skill, ECE≤.05, logistic calibration slope .8–1.2, and intercept magnitude≤.10 on the untouched test cohort, with confidence intervals and minimum event counts.

If DSR is not estimable, an outcome-changing component cannot GO.

### Metric-governance gate — C6

C6 is not judged by the DSR it is trying to repair. It passes only numerical/reference validation: analytic fixtures, Monte Carlo coverage/error checks, an independent reference implementation, and complete trial-registry reconstruction. DSR/returns for any underlying strategy are reported but governed by that strategy's own component gate.

### Safety-invariant gate — C7 and C8

C7/C8 do not need positive Sharpe to fail closed. They require formal/property tests, ≥30 affected shadow events, 100% enforcement of the safety invariant, zero false blocking outside the specified scope, and a reported opportunity-cost/DSR/drawdown analysis where estimable. They may receive only an explicitly labeled **safety GO**, never a return-superiority claim. If the natural affected sample is unavailable, status is CONTINUE and the permissive path stays disabled.

**NO-GO** occurs immediately on leakage, lineage mismatch, unregistered tuning, a false hard-gate promotion, or a breached safety stop. Metric failure at fixed maturity is NO-GO; inadequate sample/precision is CONTINUE, which still means no production change.

## Component protocols

### C1 — Calibrated selective-recommendation challenger

**Hypothesis:** a held-out calibrated probability and selective-risk layer improves probability accuracy and ranking utility without changing hard-gate actionability.

**Minimum sample and duration**

- separate frozen training, calibration, and untouched prospective test cohorts under the purged design; no outcome appears in more than one role;
- ≥100 independent closed clusters in the untouched test cohort and ≥30 observations in every material outcome class (`target-first`, `stop-first`, `timeout/exception`); the manifest defines “material” before collection and no class is merged after seeing results;
- five score bins are fixed from the calibration cohort and require ≥20 untouched-test observations each; otherwise continue collecting;
- ≥30 clusters in every predeclared score region that could alter displayed recommendation state;
- expected 4–8+ months if the full screened universe is logged; 6–12+ months if only late-stage candidates qualify.

**Primary metrics**

- multiclass/competing-risk Brier skill, log loss, ECE, slope/intercept, risk–coverage;
- calibrated target/stop/timeout probabilities and direct or competing-risk expected net R versus realized net R; planned-versus-executed setup/slippage;
- counterfactual top-k net return, corrected DSR, and max drawdown versus current ranking.

**Predeclared GO**

- universal and outcome-changing gates under a predeclared non-inferiority claim, plus Brier skill ≥5% versus the frozen regime/base-rate model;
- ECE point estimate ≤.05 and upper 95% issuer/date-block bound ≤.08;
- no hard-rejected candidate becomes actionable;
- simultaneous confidence bands show risk–coverage is non-inferior to control at every control operating point and strictly better at one calibration-cohort-selected operating point.

**NO-GO / revert**

- NO-GO for calibration drift, wider false-confidence tails, or any action override;
- revert by removing the probability/rank overlay and retaining deterministic status/reasons. Historical shadow fields remain for audit.

### C2 — Discount-rate decomposition and provenance

**Hypothesis:** separating default-free IDR Rf, mature ERP, and lambda×CRP removes sovereign-risk double counting and improves valuation-conditioned decisions.

**Frozen construction**

- primary trial: `Rf_IDR = latest-point-in-time 10y IDR government yield − latest-point-in-time Damodaran rating default-spread estimate`; `Ke = Rf_IDR + control_beta × mature_ERP + 1.0 × rating_CRP + unchanged_control_sector_premium`;
- government yield/default spread/CRP must have compatible currency/tenor conventions. Yield expires after two business days; the country-risk workbook expires at its announced next update or 190 calendar days, whichever comes first. Only information publicly available at the signal timestamp is allowed; expired/mismatched input yields `ABSTAIN_SOURCE_MISMATCH`;
- control beta is held fixed to isolate sovereign-risk construction; beta estimation, firm-specific lambda, and sector-premium changes are separate future trials;
- CDS construction is a separately registered sensitivity/trial and cannot be selected after seeing outcomes.

**Minimum sample and duration**

- ≥30 independent closed **valuation-sensitive** clusters where the challenger changes FV band, risk code, rank, or decision; target 60;
- ≥2 market regimes and ≥2 valuation sectors;
- likely 6–12+ months. A 3–15 day outcome validates swing-decision impact, not intrinsic-value truth; FV forecast accuracy also needs a separately approved longer-horizon study.

**Primary metrics**

- complete numerical reconciliation of rating and CDS variants;
- paired FV band/rank/decision changes, target-before-stop hit rate, predicted expected net R versus realized net R, planned/executed geometry, DSR, and max drawdown;
- sensitivity to beta, lambda, risk-free source date, and CRP source date;
- calibration of “undervalued” buckets against forward outcomes, with no claim that short-horizon return equals intrinsic-value realization.

**Predeclared GO**

- two independent reviewers reproduce every formula/input from source vintages;
- universal and outcome-changing **superiority** gate and no source-date/tenor/currency splice;
- primary rating path alone owns the GO decision; CDS remains a registered sensitivity;
- rating/CDS sensitivity is considered acceptably stable only when absolute Ke difference is ≤75 bp and ≥90% of affected candidates retain the same FV-band direction and actionability direction. These are predeclared project tolerances, not literature constants.

**NO-GO / revert**

- NO-GO on irreproducible inputs, any source mismatch, sensitivity outside the numeric tolerance, or worse paired risk;
- revert to the frozen control formula, flag its ERP as stale/method-limited, and keep challenger valuation advisory-only.

### C3 — Finance-domain sentiment calibration

**Hypothesis:** a finance-domain, context-aware model with an abstain option is better calibrated than the current general-domain checkpoint and improves decisions only when sentiment evidence is reliable.

**Minimum sample and duration**

- ≥300 time-split Indonesian finance texts after URL/content/syndication deduplication, with no ticker/event leakage between splits;
- two blinded annotators label the untouched test set and at least 20% of earlier cohorts; predeclared label guide, Krippendorff's alpha ≥.75, and blinded third-party adjudication resolve disagreement before model outputs are revealed;
- ≥30 independent closed signal clusters whose debate input changes; target ≥60;
- untouched test set has ≥50 examples per sentiment class; sparse classes are collected rather than oversampled into the final test set;
- estimated 3–6+ months for text benchmark and 4–8+ months for signal outcomes.

**Primary metrics**

- macro-F1, per-class precision/recall, Brier, ECE, abstention coverage, context/ticker error slices;
- issuer/date-block confidence intervals for macro-F1, class recall, and Brier improvement;
- debate confidence delta, target-before-stop hit rate, predicted expected net R versus realized net R, planned/executed geometry, corrected DSR, and drawdown;
- current checkpoint, finance challenger, keyword, LLM-only, and base-rate comparisons.

**Predeclared GO**

- universal and outcome-changing non-inferiority gate; lower confidence bounds must support macro-F1 ≥.75, every class recall ≥.65, and ≥5% Brier improvement versus current checkpoint, while the ECE upper confidence bound must be ≤.05;
- no outcome influence when out-of-domain or below calibrated confidence;
- signal-level paired net R satisfies the registered non-inferiority margin and the outcome-changing DSR rule.

**NO-GO / revert**

- NO-GO on class collapse, context leakage, raw-softmax overconfidence, or deterioration of paired decisions;
- revert to disabled prior/LLM-only control; retain sentiment model as an annotated research field.

### C4a — Regime-model stability

**Hypothesis:** one frozen persistence-penalized regime challenger reduces noisy state switching without weakening the conservative resolver.

**Minimum sample and duration**

- ≥30 dependence-adjusted transition-sensitive closed signal clusters;
- ≥504 untouched OOS trading days after warm-up, all three states observed, and ≥5 independently dated transition episodes;
- state-label alignment is learned on training data only; point-in-time transition labels and detection-cost weights are frozen before test;
- expect **2–3+ years**, not months. Without all three states, a full production GO is impossible; the report may only describe the observed-state scope.

**Primary metrics**

- transition count, dwell-time distribution, posterior entropy, five-day flip rate, and confidence intervals;
- detection delay/false-transition cost against independent point-in-time transition labels;
- regime-conditioned target-before-stop rate, predicted expected net R versus realized net R, DSR, and daily-NAV max drawdown;
- decision disagreement with the current HMM/resolver.

**Predeclared GO**

- universal and outcome-changing non-inferiority gate;
- upper confidence bound supports at least 20% lower five-day flip rate, while upper confidence bound on added detection delay is ≤2 trading days;
- no UNKNOWN/low-posterior state becomes more permissive than control; all three states and five transitions are present.

**NO-GO / revert**

- NO-GO if states are incomplete, transitions too few, labels become less interpretable, or missing/uncertain states are more permissive;
- revert to current HMM plus conservative resolver.

### C4b1 — Point-in-time foreign-flow input

**Hypothesis:** adding fully point-in-time foreign flow improves regime information without data leakage or more-permissive missing-data behavior.

**Minimum sample/duration:** ≥30 affected independent closed clusters, ≥2 predeclared foreign-flow strata, and ≥252 OOS daily source observations; likely 12+ months.

**GO:** universal and outcome-changing non-inferiority gate, 100% source/as-of lineage, <1% unexpected missingness, strict UNKNOWN/control behavior when expired, and a predeclared regime-information metric improvement with confidence interval. **Revert:** omit foreign flow and preserve it as display-only provenance.

### C4b2 — Dated MSCI review state

**Hypothesis:** an official-source, expiring MSCI state record is safer and more accurate than a permanent Boolean.

**Minimum sample/duration:** ≥30 affected independent closed clusters across at least two official source-state vintages; likely 12+ months and potentially longer because review changes are rare. Repeated daily copies of one MSCI announcement count as one source-state event.

**GO:** universal and outcome-changing non-inferiority gate, 100% official-source/date/expiry lineage, zero stale-active days after expiry, and no expired/missing state more permissive than control. If two source-state vintages do not occur, status remains CONTINUE. **Revert:** freeze the official state as non-decisional context and use the conservative resolver.

### C5 — Actual IDX4 factor challenger

**Hypothesis:** point-in-time factor portfolios matching the paper's OCF/market-equity and EBIT/book-enterprise-value definitions add incremental net information beyond current scores.

**Entry prerequisite**

Current internal OCF/RNOA validation has zero usable periods. Collection does not begin until:

- ≥80% point-in-time coverage in the eligible non-financial universe;
- restatement-aware source dates and no forward-filled future filings;
- variable definitions and 2×3 factor construction reproduce the paper.

**Minimum sample and duration**

- a power calculation using the predeclared IC effect, variance, and serial dependence determines the final requirement, never fewer than 36 untouched OOS monthly cross-sections, 100 eligible names in **every** cross-section, and 30 independent closed signal clusters;
- ≥2 market regimes, with missingness/coverage compared by size, sector, survival, and subsequent return;
- expected **3–5+ years**. A 30-trade or 12-month result cannot validate a monthly cross-sectional factor model.

**Primary metrics**

- coverage, turnover, factor returns, IC and HAC/block-bootstrap IC inference, factor exposure stability;
- incremental target-before-stop rate/net R, predicted expected net R versus realized net R, corrected DSR, PBO when estimable, and daily-NAV max drawdown;
- current score, paper-faithful factor, and simple sector-neutral characteristic baselines.

**Predeclared GO**

- universal and outcome-changing superiority gate; coverage remains ≥80% and ≥100 names in every cross-section;
- mean OOS IC >.03 with HAC t-stat ≥3.0 for the pre-registered discovery family—both are conservative **project policies**, not universal Harvey–Liu–Zhu thresholds;
- DSR≥.95 after all tried variants are counted. PBO uses fixed CSCV only when there are at least eight registered configurations and eight non-overlapping time blocks; partitions and selection statistic are frozen, and PBO<.20 is a project policy. Otherwise report `PBO_NOT_ESTIMABLE` without substituting a favorable number;
- net performance survives costs and sector/size neutrality checks.

**NO-GO / revert**

- NO-GO on missing point-in-time coverage, insufficient power, t-stat/DSR failure, estimable PBO≥.20, missingness bias, or benefit explained only by size/sector;
- revert to current characteristic fields with no factor-model claim and zero outcome influence for the challenger.

### C6 — DSR/trial-governance correction

**Hypothesis:** a single canonical implementation plus complete trial accounting prevents false promotion from multiple testing.

The canonical path uses daily marked-to-market NAV returns with a predeclared HAC/Newey–West autocorrelation adjustment capped at lag 14 (the maximum holding horizon minus one) for Sharpe variance/effective sample size. A fixed-calendar-origin, non-overlapping 15-day return construction is the required sensitivity. Both conventions are frozen and validated on autocorrelated fixtures before any DSR-based GO decision.

**Minimum sample and duration**

- 100% reconstruction of every configuration in the relevant research family;
- ≥30 independent frozen return-series fixtures spanning Gaussian/non-Gaussian, skewed, fat-tailed, autocorrelated, one-trial, and multi-trial cases; plus ≥10,000 Monte Carlo replications per coverage case;
- ≥30 independent closed clusters for any underlying outcome component; its stricter minimum still applies;
- implementation reconciliation can take weeks, but market validation still takes the associated component's full 2-month-to-multi-year window.

**Primary metrics**

- numerical agreement against hand-worked paper examples and between implementations;
- Monte Carlo Type-I error/coverage and numerical stability across the frozen fixture grid, including positive/negative autocorrelation and overlapping-holding processes;
- effective trial count, SR distribution, skew/kurtosis, DSR, and PBO where defined;
- target-before-stop rate, expected-net-R calibration, max drawdown, and probability calibration are reported under the underlying component, not used to validate the DSR formula itself.

**Predeclared GO**

- canonical and independent reference implementation agree within `1e-6` on frozen fixtures;
- empirical Monte Carlo coverage/error falls inside the predeclared 95% simulation interval around nominal behavior for every fixture family, and HAC/non-overlapping sensitivity does not reverse a GO conclusion;
- trial registry completeness =100%; no OOS window is silently substituted for a tried strategy;
- the metric-governance gate passes independently; any underlying challenger must separately satisfy its own DSR and component GO gate.

**NO-GO / revert**

- NO-GO on an incomplete trial family, divergent implementations, or undefined independence assumptions;
- revert DSR to an advisory diagnostic labeled `NOT_PROMOTION_GRADE`; raw Sharpe cannot replace it.

### C7 — Missing-liquidity abstention

**Hypothesis:** abstaining when capacity is unmeasurable removes unsafe bypass-path sizing without material loss of valid opportunity.

**Minimum sample and duration**

- ≥30 independent candidate events that reach the downstream governor with missing ADT/capacity and would otherwise remain potentially sizeable;
- target ≥60 because missingness may be path-specific;
- likely 6–12+ months. If zero/rare events occur, keep fail-closed behavior proposed but automatic promotion remains unavailable under this protocol.

**Primary metrics**

- missing-data incidence/root cause/recovery latency;
- false-missing rate, abstention coverage, counterfactual hit/net R, DSR and drawdown where estimable;
- downstream sizing attempts with missing data (must be zero in challenger).

**Predeclared GO**

- universal and safety-invariant gates; formal/property tests and shadow records show 100% of truly missing mandatory-liquidity events abstain;
- false-missing rate <1%; ≥95% of recoverable source failures resolve within the predeclared refresh SLA;
- no evidence that a valid measured-liquidity candidate was incorrectly blocked;
- DSR/drawdown/opportunity cost are reported where estimable but are not prerequisites for this fail-closed safety GO; no return superiority may be claimed.

**NO-GO / revert**

- NO-GO on false missingness or source outages that turn the entire universe unavailable;
- revert to control with a global execution halt for the affected path—not silent fail-open—and repair source acquisition.

### C8 — Remove/disable the reachable `momentum_play` exemption

**Hypothesis:** fail-closed ordinary overvaluation handling removes an undocumented bypass without changing unrelated candidates. This protocol does **not** test enabling an exemption; the R/R 2.5 and half-size code comments are unsupported and out of scope.

**Minimum sample and duration**

- ≥30 independent naturally occurring or pre-registered replay events in which an overvalued candidate carries `momentum_play=True`, plus ≥30 matched overvalued records without the flag;
- the CIO prompt/schema make the branch reachable; replays may test it but cannot inject the flag into live decisions;
- likely 6–12+ months for natural events. With zero occurrences, outcome is CONTINUE and the permissive branch stays disabled.

**Primary metrics**

- fail-closed enforcement rate and unintended decision changes outside the flagged branch;
- opportunity cost, target-before-stop rate, net R, DSR, and drawdown where estimable, without using good returns to justify an exemption;
- property tests proving the flag cannot bypass snapshot, liquidity, ex-date, noise, circuit-breaker, or portfolio gates.

**Predeclared GO**

- universal and safety-invariant gates;
- 100% of flagged overvalued records follow the ordinary fail-closed overvaluation path;
- zero unrelated-record decision changes and zero safety-gate bypasses. DSR/opportunity cost are descriptive only.

**NO-GO / revert**

- any bypass or unrelated change is immediate NO-GO;
- revert to the frozen control while keeping the exemption disabled by configuration; any future enabling proposal requires new research and approval.

## Reporting cadence and review

- **Daily:** automated integrity manifest, opportunity-set parity, source freshness, and safety-stop check.
- **Weekly:** blinded operational report—counts, missingness, maturity schedule, no GO interpretation.
- **Monthly:** blinded data-quality/sample-maturity report and trial-registry checksum; no efficacy metric or GO interpretation unless an approved always-valid stopping rule was pre-registered.
- **At the fixed terminal date after full maturity:** unblind all common metrics, independently reproduce, and sign GO/NO-GO. No mid-run threshold tuning.

Reports must show negative and null results. A failed challenger is retained as an immutable artifact so the same idea is not rediscovered and selectively retested.

## Immediate stop conditions

Stop the affected challenger, preserve artifacts, and mark NO-GO on:

- look-ahead, survivorship, revised-fundamental, or timestamp leakage;
- mismatched control/challenger opportunity set;
- source/hash/protocol-manifest corruption;
- any hard-gate false promotion or paper order sent to live execution;
- unregistered model/configuration selection;
- challenger drawdown breaching the predeclared safety envelope before maturity;
- human-readable report claiming a shadow result is live/trusted.

The control continues unless its own live safety mechanism halts it.

## Promotion and rollback

After a GO result, promotion is still not automatic. It requires explicit user approval, an implementation review, and a small canary scope. Promote one component at a time with:

- default-off feature flag;
- frozen rollback commit/content manifest;
- dual-write of old and new decisions;
- production drift alarms using the same calibration/risk metrics;
- automatic disable on lineage failure, false hard-gate promotion, or calibration/drawdown breach.

On rollback, restore the previous control immediately, keep all new fields as non-decisional evidence where safe, and open a new protocol ID for any revised challenger.

## Final interpretation rule

- **GO** means the predeclared evidence bar was met for one frozen component on the specified scope.
- **NO-GO** means it failed; do not tune on the test set and relabel the same run.
- **CONTINUE** means insufficient independent evidence; operationally it is still “do not promote.”

No result in this protocol authorizes a lower R/R, momentum, SIDEWAYS, debate-eligibility, or portfolio-risk threshold.
