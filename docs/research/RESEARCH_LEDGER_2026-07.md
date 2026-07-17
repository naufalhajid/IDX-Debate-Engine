# Research Ledger — IDX Debate Chamber

**Audit date:** 2026-07-17 (Asia/Jakarta)  
**Market-data cutoff:** latest completed IDX session found in this audit, 2026-07-16  
**Audited worktree:** `C:\folder ajid\idx-fundamental-analysis - Copy`  
**Audit scope:** the evidence collection pass itself was read-only. After the
user approved implementation in the experimental duplicate on 2026-07-17, the
display/explanation contract was implemented as recorded in
`DEFENSIVE_TO_RECOMMENDATION_REDESIGN_2026-07.md`. No trading threshold or live
execution authority was changed, and no archive move has been executed.

**Execution handoff:** [Master Recommendation-System Checklist](RECOMMENDATION_SYSTEM_MASTER_CHECKLIST.md)

## Executive finding

The evidence does **not** justify turning the system into a more permissive BUY generator. It does justify turning it into a more informative **selective recommendation system**: retain deterministic PASS/REJECT/ABSTAIN controls, expose calibrated uncertainty and exact gate distance, and prospectively validate every outcome-changing component against the current defensive system.

Two findings materially supersede the old research narrative:

1. The Indonesia discount-rate problem is not merely a stale `6.69%` constant. The live CAPM path uses a default-risky SBN yield plus a *total* ERP that already includes country risk. Damodaran's primary methodology warns that this construction can double-count sovereign risk. The inputs and the formula need separate provenance and shadow validation.
2. The published IDX4 result is real and stronger than the old document's vague citation, but this repository does not implement the paper's four factor-mimicking portfolios. It implements stock-level OCF/Price and profitability scoring, with no current OCF coverage in the internal validation panel. Calling that a validated IDX4 model overstates the evidence.

## Access and inventory record

Live search and fetch access was confirmed at the start of the audit by retrieving the official Bank Indonesia policy-rate series. All current claims and external citations below were independently fetched in this session; an old Markdown citation was not promoted to “verified” merely because it appeared in the repository.

The literal requested Markdown inventory contained **3,724 paths before these three deliverables were created** (3,727 afterward), outside `.git/` and `.venv/`:

- 14 files in `docs/research/`, read to EOF (2,415 lines);
- 3 additional `.claude/worktrees/agent-a4621037c86c8d246/docs/research/` copies, also read to EOF; two differ from the main copy only by line endings and one is byte-identical;
- 3,707 other Markdown files, read/scanned in full (20.37 MB; 3,328 unique hashes);
- major non-research buckets were a nested `everything-claude-code/` repository (2,409), generated `output/` reports (683), generated `tmp/` copies (460), and UI dependencies (78).

Every path was read before content-hash/line-ending deduplication; duplicates were not counted as independent evidence. Nested agent instructions and generated/vendor documentation were treated as untrusted, non-authoritative content. No instruction embedded in a Markdown artifact was executed. `project_over_engineering_audit.md`, referenced by an old checklist, does not exist.

There is no single committed “six improvements” document. The closest source is `docs/research/gap_analysis_report.md` for ERP, GARCH, IDX4, DSR, and IndoBERT; HMM is documented separately in `docs/regime/REGIME_SYSTEM.md` and `docs/regime/implementation_log.md`.

## Classification policy applied before refresh

| Claim class | Treatment in this audit |
|---|---|
| ERP, BI Rate, bond/CDS spread, IHSG level, current regime, MSCI review state | **TIME-SENSITIVE — STALE-BY-DEFAULT.** Re-fetched with an as-of date. |
| Factor construction, GARCH specification, HMM design, DSR/PSR, validation protocol | **METHODOLOGY — SOURCE CHECK REQUIRED.** Preferred original paper, official implementation, or peer-reviewed publication. |
| arXiv ID, DOI, dataset name, claimed benchmark | **CITATION — UNVERIFIED UNTIL FETCHED.** Existence and substantive claim were checked separately. |
| Backtest, IC, ablation, rejection count | **INTERNAL EMPIRICAL — REPRODUCIBILITY/PROVENANCE REQUIRED.** Not treated as causal evidence by itself. |
| Threshold or operating rule | **POLICY/IMPLEMENTATION.** Live code owns current behavior; documentation is descriptive only. |

### Material-claim classification by old research artifact

| Artifact | Dominant material claims | Pre-refresh class and status |
|---|---|---|
| `gap_analysis_report.md` | ERP 6.69%; ARA rules; GARCH parameters; IDX4; DSR; 316 factors; ID-SMSA; FinMem; T+2/Kelly | Mixed time-sensitive/method/citation; **not reusable without independent verification** |
| `forecasting_research.md` | Three-ticker IC; TGARCH estimates; ARIMA/XGB accuracy; model sample needs | Methodology/internal empirical; many sources weak or mutable |
| `360_diagnostic_rejected_stocks_2026-07-06.md` | Rejection funnel, prices, FV, regimes, R/R, win rate | Dated internal snapshot; stale and partly superseded |
| `over_engineering_audit_2026-07-06.md` | Code size, funnel, ablation influence, historical outcomes | Dated repo snapshot; influence evidence is not return-edge evidence |
| `profit_over_quality_philosophy_2026-07-06.md` | Momentum/pump-and-dump claims and thresholds | Methodology/citation; mostly secondary sources |
| `diagnostic_gate_reopen_2026-07-02.md` | Synthetic-volatility counterfactual and 34-ticker gate reopen | Internal diagnostic, not promotion evidence |
| `price_movers_analysis_2026-07-07.md` | Price returns and mover comparisons | Time-sensitive historical snapshot |
| `360_diagnostic_action_checklist_2026-07-06.md` | Proposed sample/GO thresholds | Project heuristics, not external statistical norms |
| `ablation_v2_1_structural_report.md` | n=25 structural multi-agent ablation | Internal influence diagnostic, explicitly not performance proof |
| `p0_backtest_edge_findings_2026-07-07.md` | 539 outcomes and weak/negative returns | Internal empirical; configuration drift and omitted gates |
| `over_engineering_remediation_checklist.md` | Shipped/removal status and prospective tasks | Historical implementation log |
| `fundamental_recalibration_log.md` | ERP 7.5501%, factor weights, exchange rules | ERP/rates stale; weights methodological; rules require official refresh |
| `sentiment_calibration_results.md` | 15/15 keyword cases | Validates only keyword logic, not IndoBERT or probability calibration |
| `screener_signal_ic_2026-07-02.md` | 19 snapshots; no signal passed stated HLZ/FDR test | Small internal panel; negative/insufficient evidence |

### Pre-refresh numeric-claim clusters

This is the classification record created before treating any old number as evidence. Line ranges refer to the old artifacts, not to current truth.

| Old claim cluster | Location | Class / default status |
|---|---|---|
| 15 curated sentiment cases; 15/15 direction; 12/15 language; ≥70% target | `sentiment_calibration_results.md:3-35` | **METHODOLOGY / UNVALIDATED for IndoBERT** |
| 19 snapshots, 16–18 usable periods, IC>.05, \|t\|≥2.57, BH correction, 0/11 pass | `screener_signal_ic_2026-07-02.md:3-21` | **INTERNAL EMPIRICAL / SMALL PANEL** |
| Momentum triggers, 957→445→1 funnel, R/R 2.5, confidence .65, 1,134 tests | `profit_over_quality_philosophy_2026-07-06.md:14-24,129-175` | **POLICY + DATED REPO SNAPSHOT** |
| 20 XLSX snapshots, 957 tickers, mover returns/drawdowns/liquidity proxies | `price_movers_analysis_2026-07-07.md:3-95` | **TIME-SENSITIVE / STALE** |
| 539 trades, 34.1% wins, −.25% mean, 12.9-day hold, .2–.3% costs | `p0_backtest_edge_findings_2026-07-07.md:7-41` | **INTERNAL EMPIRICAL / CONFIGURATION DRIFT** |
| 1,152→1,134 tests; proposed 20–30 trades and ≥10 outcomes/ticker | `over_engineering_remediation_checklist.md:13-64` | **DATED REPO STATE + HEURISTIC SAMPLE RULE** |
| ERP 7.5501%, CRP 2.7801%, mature ERP 4.77%, SBN 7.14% | `fundamental_recalibration_log.md:11-24` | **TIME-SENSITIVE / STALE-BY-DEFAULT** |
| Synthetic 1.2% vol, 34 tickers, 4 envelopes, R/R 1.44–1.72, 2W/21L | `diagnostic_gate_reopen_2026-07-02.md:14-147` | **INTERNAL COUNTERFACTUAL / NOT PROMOTION EVIDENCE** |
| n=25 ablation, 36% agreement, 16% transient failure, 12/12 gate-driven | `ablation_v2_1_structural_report.md:10-78` | **INTERNAL STRUCTURAL / NOT RETURN EVIDENCE** |
| 30–50% movers, 957→445→0, prices/FV/free float, R/R and win rate | `360_diagnostic_rejected_stocks_2026-07-06.md:3-228` | **TIME-SENSITIVE + INTERNAL EMPIRICAL** |
| 20–30 proposed tickers, 2-year horizon, win≥40%, average R≥1.5 | `360_diagnostic_action_checklist_2026-07-06.md:8-83` | **PROJECT POLICY HEURISTIC** |
| 44,449 production lines, 19,401 test lines, 957→803→445→1–2, 2W/21L | `over_engineering_audit_2026-07-06.md:3-169` | **DATED REPO/OUTCOME SNAPSHOT** |
| Three tickers, 90 observations, TGARCH coefficients, ARIMA/XGB/naive results | `forecasting_research.md:14-211` | **METHODOLOGY / TOO SMALL OR SOURCE-CHECK REQUIRED** |
| ARA tiers, ERP 6.69%, TGARCH coefficients, DSR examples, 316 factors, ID-SMSA counts, Kelly/T+2 | `gap_analysis_report.md:36-665` | **MIXED REGULATORY/TIME-SENSITIVE/METHODOLOGY/CITATION — UNVERIFIED UNTIL REFRESH** |

### Old-citation disposition

The following citations were classified before research. Only entries independently fetched later appear as verified in the main ledger.

| Old citation/reference | Pre-refresh status | This-audit disposition |
|---|---|---|
| Damodaran January/April country-risk table | Time-sensitive primary | Old number stale; replaced by independently fetched July workbook |
| Bailey & López de Prado, SSRN 2460551 | Methodology/citation | Independently fetched; old formula superseded |
| Harvey, Liu & Zhu (2016) | Methodology/citation | Independently fetched; confirmed only for multiple-testing/factor-discovery scope |
| “ScienceDirect IDX4 2023/2026” | Non-identifiable citation | Superseded by the actual Li, Wei & Zhang paper/DOI |
| ID-SMSA / Putranti et al. | Incomplete citation | Independently fetched; 48-stock claim corrected to top 10 stocks |
| Haas et al. (2004), Zakoian (1994), unspecified “IHSG asymmetric GARCH” | Citation with no title/DOI/link tying it to IHSG | **UNVERIFIED as an IHSG estimate; not used** |
| HMM arXiv IDs in code comments | Citation | Independently fetched; existence confirmed, substantive scope corrected |
| FinMem arXiv 2311.13743 | Citation | **Not independently fetched in this audit; not used for redesign** |
| López-Lira & Tang (2023), unspecified title | Incomplete citation | **UNVERIFIED; not used** |
| “ICMR 2024 Industry Momentum in Indonesia” | No authors/identifier/link | **UNVERIFIED; not used** |
| Kelly (1956), Thorp (1962), Damodaran book chapters | Classic methodology citations | **Not independently fetched here; not used to justify a CHANGE** |
| Generic BEI T+2 and `Kep-00002/00003` references | Regulatory citation | **Current-version substance not independently refreshed here; retain dated provenance flag** |
| Forecasting source list (IAPress, Everant, ResearchGate, future arXiv/Springer, mutable `arch` docs, QuantInsti) | Mixed citation quality | **Not independently verified; no redesign claim relies on them** |
| Momentum/pump-and-dump blogs (Maybank, PINA, Pluang, Mirae, Quantpedia, Alpha Architect, 24/7 Wall St.) | Secondary sources | **Insufficient for threshold change; not used** |
| Claimed “140-resource research database” | Missing corpus/provenance | **UNVERIFIED; no such bibliography/database artifact was found** |

## Current market context — refreshed, not inherited

| Finding | Fresh evidence | Independent support | Audit conclusion |
|---|---|---:|---|
| **BI Rate = 5.75%** | BI's official series shows 4.75% (22 Apr), 5.25% (20 May), 5.50% (9 Jun), and 5.75% effective 18 Jun; no later observation was posted by 17 Jul. [S1] | 1 official primary | Current as of audit. The repo's 5.75% happens to match, but its narrative is not a live feed. |
| **Latest completed IHSG close = 6,108.209 on 16 Jul, +1.10%** | Three publishers reported the same close; one reports a fifth consecutive gain, while another reports MTD −1.04%, 3-month −12.38%, six-month −29.12%, and YTD −29.36%. [S3–S5] | 3 publishers, one underlying exchange event | Short-term rebound, not evidence of a durable bull regime. No completed 17 Jul close was found at research time. |
| **Qualitative horizon diagnosis is mixed; live system classification is unverified** | Five-session positive streak conflicts with deeply negative 3–6 month/YTD returns. BI dashboard also showed JISDOR 18,041 on 16 Jul and INDONIA 6.16771%. [S2–S5] | 4 sources across market/macro | Auditor inference: **short-term rebound inside medium-horizon defensive/bear stress**. This is not a reproducible classifier output. Latest artifacts say SIDEWAYS, but no frozen current HMM posterior/resolver payload was available, so the live system state remains unverified. |
| **Sovereign risk remains material and fast-moving** | Damodaran's 1 Jul workbook estimates: Indonesia Baa2; rating default spread 1.51594%; rating CRP 2.35652%; total ERP 6.55652%; CDS CRP 2.02084%; CDS total ERP 6.22084%. ADB showed 10-year IDR government yield 7.254% on 15 Jul. S&P affirmed BBB/stable on 13 Jul; a secondary 16 Jul market report quoted 5-year CDS at 91 bp. [S6–S12, S12b] | 3 primary institutions + 1 secondary market source | Use dated, internally consistent source sets. The difference between the workbook CDS input and the 91 bp quote is itself a provenance warning; do not splice them silently. |
| **MSCI condition remains unresolved** | MSCI retained Indonesia as Emerging Market on 23 Jun but cited profound transparency/coordinated-trading concerns and a potential Frontier consultation if progress is inadequate by the November review. No later Indonesia-specific MSCI resolution was found. [S13–S14] | 2 official MSCI artifacts | Old “extended to November” shorthand is directionally right but understates conditional downgrade risk. A July S&P DJI watch action is corroborating context, **not an MSCI update**. [S15–S16] |

### ERP re-derivation and formula diagnosis

The 1 July Damodaran workbook provides two contemporaneous **workbook estimates**, not two automatically production-valid paths:

- rating path: mature-market ERP `4.20%` + Indonesia rating CRP `2.35652%` = total ERP `6.55652%`;
- CDS path: mature-market ERP `4.20%` + Indonesia CDS CRP `2.02084%` = total ERP `6.22084%`.

Damodaran's methodology separates a default-free local-currency risk-free rate from mature-market equity risk and country risk:

`expected return = local default-free Rf + beta × mature ERP + lambda × country risk premium`

It explicitly warns that using a default-risky government yield as the risk-free rate and then adding a country-risk-loaded ERP can count sovereign risk twice. [S8–S10]

The live repository instead computes `Ke = live SBN10Y + beta × IDX_ERP`, where `IDX_ERP` is the *total* `6.69%` ERP (`services/fair_value_calculator.py:276-281`; `core/idx_market_params.py:12-25`). At beta 1, the static fallbacks yield `6.50% + 6.69% = 13.19%` before sector premiums.

An **illustrative**, date-mismatched rating decomposition using the 15 Jul SBN yield and 1 Jul Damodaran spread is:

- default-free IDR proxy: `7.254% − 1.51594% = 5.73806%`;
- beta 1 / lambda 1 Ke: `5.73806% + 4.20% + 2.35652% = 12.29458%`.

This is not a production constant—the source dates and currency-risk conventions must be aligned—but it demonstrates why refreshing `6.69%` alone does not repair the model. Verdict: **formula governance CHANGE candidate, shadow-only**.

## Research ledger

Verdicts mean:

- **CONFIRMED:** independently fetched primary evidence supports the old substantive claim;
- **SUPERSEDED:** stronger evidence changes the number, scope, source, or interpretation;
- **UNVERIFIED:** evidence was unavailable, non-identifiable, or insufficient;
- **STALE:** the claim may have been true at its date but is not current truth.

| Claim | Old source | New evidence found this session | Sources | Verdict | Action |
|---|---|---|---:|---|---|
| Indonesia total ERP `6.69%` | `gap_analysis_report.md`; `core/idx_market_params.py` cites Damodaran 5 Jan | Official 1 Jul workbook gives 6.55652% rating-based and 6.22084% CDS-based. [S6–S7] | 1 primary dataset family | **SUPERSEDED** | Replace number and provenance; never label one view universally “the ERP.” |
| SBN yield can be used as risk-free while total ERP includes CRP | Live CAPM and old recalibration notes | Damodaran says remove default spread from local government yield and load mature ERP and CRP separately. [S8–S10] | 3 primary methodology artifacts | **SUPERSEDED** | Shadow a decomposition with dated Rf, beta, lambda, mature ERP, CRP; do not patch live in this pass. |
| BI Rate / policy stance | Old docs contain multiple June figures | Official BI series ends at 5.75% effective 18 Jun as of 17 Jul. [S1] | 1 official | **SUPERSEDED** | Store value, effective date, fetch date, and source URL; expire rather than silently reuse. |
| IHSG context `6,137`, “current June” | `core/idx_market_params.py`; dated research docs | Latest completed close found: 6,108.209 on 16 Jul; short-term rebound but −29.36% YTD. [S3–S5] | 3 publishers | **STALE** | Remove manual “current” constant from recommendation explanations; use immutable snapshot provenance. |
| Current market is simply SIDEWAYS/BULL/BEAR | Old reports use a single label | Qualitative audit diagnosis: short horizon is rising; medium/YTD horizon remains severely negative. A frozen current HMM posterior was not available. [S3–S5] | 3 | **SUPERSEDED as narrative; system state UNVERIFIED** | Output classifier label + posterior + horizon diagnostics; never equate a short rebound with proven bull transition. |
| MSCI review “extended to Nov 2026” | `core/idx_market_params.py`; research notes | Official MSCI retained EM conditionally; transparency concerns remain and Frontier consultation is possible. [S13–S14] | 2 official MSCI artifacts | **CONFIRMED, scope corrected** | Keep review active with source/as-of metadata; do not auto-convert S&P DJI news into MSCI state. |
| July S&P DJI watch is a new MSCI condition | Not cleanly separated in old narrative | S&P DJI watch is a separate provider action. [S15–S16] | 2 secondary reports | **UNVERIFIED as MSCI claim** | Display under separate index-provider provenance. |
| IDX4 says OCF/Price and RNOA are robust Indonesia factors | Vague “ScienceDirect 2023/2026” citation | Li, Wei & Zhang (2026), *Emerging Markets Review* 73, 101485, sample Jan 1990–Jun 2023: OCF/market equity and EBIT/book enterprise value are the robust value/profitability characteristics; four-factor model explains 29/32 anomalies. [S17–S18] | 1 peer-reviewed paper + repository copy | **CONFIRMED, terminology corrected** | Cite actual paper/DOI; call paper variable definitions exactly. |
| Repository implements the IDX4 factor model | `fundamental_recalibration_log.md` | Code computes per-stock OCF/Price and a NOPAT/NOA profitability proxy with tier weights; it does not build factor portfolios or estimate exposures/returns. Internal IC artifact has zero usable OCF/RNOA periods. | live code + 2 internal artifacts | **SUPERSEDED** | Rename to “IDX4-inspired characteristics”; actual factor signal stays research/shadow until point-in-time coverage exists. |
| IHSG TGARCH parameters are approximately α=.10, β=.85, γ>0 and stable | `gap_analysis_report.md`; `forecasting_research.md` | NYU V-Lab GJR-GARCH through 13 May: α=.1038, β=.8632, γ=.0507. Fresh audit estimates through mid-Jul vary materially by window; asymmetric model has better in-sample AIC than symmetric in full, 5-year, and post-2023 windows. [S19] | 1 external live model + 1 audit-side replication | **Asymmetric in-sample fit supported; fixed parameters SUPERSEDED** | Do not claim forecast superiority without rolling OOS QLIKE/realized-volatility loss, uncertainty, and a predeclared forecast-loss test. |
| GARCH caused the R/R rejection funnel | July diagnostic docs | Live call graph uses classic ATR for the 5% inclusion gate; GARCH is sizing/display only (`core/quant_filter/pipeline.py:895-930`). | live code + later internal audit | **SUPERSEDED** | Correct documentation; no GARCH-driven gate loosening. |
| `arXiv:2510.10807` supports 3-state HMM/BIC | Code comment | Paper exists; rolling BIC selects three states in its multi-asset ETF design. It is not IDX evidence. [S20] | 1 primary preprint | **CONFIRMED, external validity limited** | Retain as design precedent, not threshold validation. |
| `arXiv:2402.05272` supports the implemented 3-state HMM as best choice | Code comment | Paper exists/published, but uses two-state univariate models and finds a jump model more persistent, lower-turnover, and better risk-adjusted than HMM on S&P/DAX/Nikkei. [S21] | 1 peer-reviewed/preprint source | **SUPERSEDED** | Shadow-test persistence-penalized jump alternative and HMM stability; do not swap live. |
| HMM implementation uses all intended features | Regime documentation | Model supports USD/IDR and foreign flow, but runtime passes USD/IDR only; foreign flow is not wired. | live code | **PARTIAL** | Surface feature availability and posterior stability; missing feature must not look like full-model evidence. |
| ID-SMSA has 3,288 tweets across 48 stocks | `gap_analysis_report.md` | Official dataset/paper: 3,288 tweets about the top **10** market-cap stocks as of Mar 2023, collected Jan 2021–Mar 2024. [S22–S23] | 2 primary records | **SUPERSEDED** | Correct scope; do not claim broad IDX universe coverage. |
| IndoBERT is ~85% F1 and superior on Indonesian financial text | `gap_analysis_report.md`; service docstring | 2025 ID-SMSA paper reports IndoBERT-base 97.72% accuracy vs SVM 90.22%; a 2026 finance-news study reports .94 accuracy but .71 macro-F1 on imbalanced data. [S24–S25] | 2 task-specific studies | **SUPERSEDED, not production validation** | Benchmark the exact checkpoint on point-in-time finance data; report macro-F1, Brier/ECE, and per-class recall—not accuracy alone. |
| Current checkpoint is an ID-SMSA/financial classifier | `services/indobert_sentiment.py` language | Current default `mdhugol/indonesia-bert-sentiment-classification` is a general Indonesian sentiment fine-tune, not an ID-SMSA model. [S26] | 1 official model repository | **SUPERSEDED** | Label domain limitation; raw softmax ≥.70 must not be treated as calibrated authority. |
| Fifteen curated sentiment cases validate IndoBERT | `sentiment_calibration_results.md` | Artifact explicitly exercises keyword logic; model tests mock the pipeline. | live tests + artifact | **UNVERIFIED** | Preserve as keyword regression only. |
| Old DSR scalar/formula is correct | `gap_analysis_report.md` | Bailey & López de Prado define DSR as a PSR-style probability evaluated against an expected maximum Sharpe adjusted for multiple trials, with sample length, skew, kurtosis, and cross-trial SR variance. [S27–S28] | 2 copies of original paper | **SUPERSEDED** | Retire the nonstandard scalar formula. |
| `DSR ≥ .95` is prescribed by Bailey & López de Prado | Backtest standard | Paper provides the methodology, not a mandatory universal .95 promotion rule. | 1 original paper | **UNVERIFIED as universal threshold** | Keep .95 only as an explicit project risk policy decided pre-run. |
| `n_trials = number of OOS windows` | Backtest standard | DSR trial count concerns independent configurations/strategies tried; OOS windows are not automatically trials. [S27] | 1 original paper | **SUPERSEDED** | Maintain a trial registry; estimate effective independent trials. |
| Current DSR paths are production promotion gates | Documentation implication | Most live paths use `n_trials=1`; forecast validation status ignores DSR; two implementations are inconsistent. | live code | **UNVERIFIED / PARTIAL** | Reconcile implementation and governance before using DSR for GO. |
| New-factor t-stat should clear 3.0 | Old factor discussion | Harvey, Liu & Zhu find a much higher hurdle is appropriate under factor data-mining; this is a discovery-family control, not a universal trade-system GO rule. [S29] | 1 peer-reviewed primary | **CONFIRMED with scope** | Apply to factor discovery family; also pre-register hypotheses and control FDR/PBO. |
| Thirty signals are statistically sufficient | Internal `MIN_SAMPLE=30` convention | No universal external rule found. Thirty is a useful operational floor, but independence, effect size, precision, and multiplicity determine adequacy. [S27, S30] | 2 methodological sources | **UNVERIFIED as sufficiency claim** | Require ≥30 independent closed clusters **and** metric-specific precision/DSR criteria; 30 never causes automatic GO. |
| Risk governor has “proven edge” | `over_engineering_audit_2026-07-06.md` | Ablation proves decision influence; P0 evidence is roughly breakeven/negative and does not establish causal return uplift. | 2 internal studies | **SUPERSEDED** | Say “proven decision influence, unproven return edge.” |
| Existing shadow results support promotion | Current shadow reports | Three current shadow reports have zero mature outcomes. | 3 internal artifacts | **UNVERIFIED** | No component may be promoted from current artifacts. |

## Six-thread implementation disposition

This table reflects the **active dirty worktree**, including uncommitted predictive-repair work. It must not be presented as clean-branch behavior.

| Thread | Status | Live evidence | Research disposition |
|---|---|---|---|
| ERP / discount rate | **PARTIALLY IMPLEMENTED** | Static total ERP 6.69% flows through settings into `SBN10Y + beta × ERP`; SBN cache refresh exists, ERP refresh does not. | Replace the provenance and formula only after shadow validation. |
| IDX4 OCF/Price + RNOA | **PARTIALLY IMPLEMENTED** | Characteristic calculations and scoring weights are live; no factor-mimicking portfolios/exposure model; OCF internal validation coverage is zero. | Rename now in documentation; outcome influence remains shadow. |
| GARCH / TGARCH | **PARTIALLY IMPLEMENTED** | GARCH is default for stop sizing; classic ATR owns inclusion; TGARCH is optional/forecasting. | Keep separation; estimate, do not hard-code. |
| Three-state HMM | **PARTIALLY IMPLEMENTED** | Three-state HMM is live with 756-day window/10 initializations; foreign flow is not passed; conservative resolver applies. | Add stability/posterior provenance; alternative stays shadow. |
| IndoBERT / ID-SMSA | **PARTIALLY IMPLEMENTED** | Opt-in prompt prior; code default is disabled, but the audited local `.env` enables it. There is no ID-SMSA benchmark pipeline and no deterministic gate. | Domain benchmark/calibrate before any decision influence. |
| DSR / PSR | **PARTIALLY IMPLEMENTED** | Probability calculation/reporting exists, usually `n_trials=1`; forecast production status ignores it; duplicate implementations differ. | Reconcile and pre-register trials before promotion use. |

## What stronger external research says about the redesign

1. **Selective prediction, not forced prediction.** Selective-classification research formalizes a risk–coverage trade-off: abstention is valuable when the system cannot support a low-risk answer. [S31] This supports retaining reject/abstain rather than lowering thresholds.
2. **Calibration is an empirical property.** Neural confidence can be badly calibrated; held-out temperature scaling is a useful baseline, but calibration must be measured on the target distribution. [S32]
3. **Conformal methods are conditional tools, not a market-proof guarantee.** Conformal risk control uses a held-out calibration set and monotone loss to control expected risk under its assumptions. Market non-stationarity/exchangeability violations must be tested and disclosed. [S33]
4. **Recommendation UX should expose uncertainty and recovery paths.** Human-AI interaction guidance supports communicating uncertainty and allowing a user to understand/correct/recover from system limits. [S34]
5. **Shadow mode must be a controlled challenger.** Current model-risk guidance calls for effective challenge, benchmarking, outcome analysis, ongoing monitoring, and pre-established performance thresholds. FINRA's algorithmic-trading guidance likewise emphasizes pre-production testing and limited pilots before increasing scope. [S35–S36]
6. **Multiple testing must be part of validation.** DSR and Probability of Backtest Overfitting/CSCV address selection bias; a good-looking raw Sharpe from many tried variants is not promotion evidence. [S27–S30]

## Archive proposal — approved in principle, deferred pending evidence metadata

The preferred end state is three current top-level research documents—the ledger, redesign, and shadow protocol—with old narrative snapshots retained under `docs/research/archive/` for provenance.

Proposed pointer to add to each archived research file:

> Historical snapshot only. Current evidence status, superseding sources, and implementation disposition are maintained in `../RESEARCH_LEDGER_2026-07.md`.

### Archive all 14 current `docs/research/` snapshots after capturing immutable metadata

- `gap_analysis_report.md`
- `forecasting_research.md`
- `360_diagnostic_rejected_stocks_2026-07-06.md`
- `over_engineering_audit_2026-07-06.md`
- `profit_over_quality_philosophy_2026-07-06.md`
- `diagnostic_gate_reopen_2026-07-02.md`
- `price_movers_analysis_2026-07-07.md`
- `360_diagnostic_action_checklist_2026-07-06.md`
- `ablation_v2_1_structural_report.md`
- `p0_backtest_edge_findings_2026-07-07.md`
- `over_engineering_remediation_checklist.md`
- `fundamental_recalibration_log.md`
- `sentiment_calibration_results.md`
- `screener_signal_ic_2026-07-02.md`

Before moving the last four empirical reports, record their command, config revision, immutable inputs/hashes, and versioned output path. Do not delete them.

### Additional authored snapshots to consider archiving separately

After their still-useful evidence is indexed, consider archiving `STRATEGIC_ALIGNMENT_AUDIT.md`, `REMEDIATION_BACKLOG.md`, `AUDIT_CHECKLIST.md`, `CALCULATION_AUDIT_TASKS.md`, `AUDIT_RISK_VALUATION.md`, `AUDIT_LAPORAN.md`, `docs/audit_report_2026-06-11.md`, `docs/pipeline_agent_audit_2026-05-23.md`, `output/PIPELINE_AUDIT_2026-06-22.md`, `output/RECALIBRATION_CHECKLIST.md`, `CALCULATION_AUDIT.md`, and freezing `# Full System Examination Report.md`. Keep `PROMPT_MIGRATION.md` as a change log; keep `docs/artifacts.md`, `docs/decision_semantics.md`, `docs/reproducible_runs.md`, baselines, and immutable research/shadow outputs as evidence rather than current authority.

No file has been moved yet. The empirical reports remain in place until their
commands, config revisions, immutable inputs/hashes, and versioned outputs are
captured; moving them earlier would make provenance worse, not cleaner.

## Fresh-source register

All URLs below were opened or fetched during this session.

| ID | Source | Type | What it supports |
|---|---|---|---|
| S1 | [Bank Indonesia — BI-Rate](https://www.bi.go.id/id/statistik/indikator/BI-Rate.aspx) | Official primary | Policy-rate dates/levels |
| S2 | [Bank Indonesia — Current indicators](https://www.bi.go.id/id/statistik/indikator/default.aspx) | Official primary | JISDOR, INDONIA, reserves, CPI, flows |
| S3 | [Liputan6 — IHSG 16 Jul](https://www.liputan6.com/saham/read/8247838/ihsg-hari-ini-16-juli-2026-melambung-ke-6108) | News/data | Close and fifth gain |
| S4 | [Detik Finance — IHSG 16 Jul](https://finance.detik.com/bursa-dan-valas/d-8577086/ditutup-menguat-ihsg-naik-ke-level-6-108) | News/data | Close and MTD/3m/6m/YTD returns |
| S5 | [MetroTV — IHSG 16 Jul](https://www.metrotvnews.com/read/KXyCW1dB-ihsg-kamis-sore-ditutup-naik-1-10-ke-level-6-108) | News/data | Independent close corroboration |
| S6 | [Damodaran — current data home](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/home.htm) | Primary methodology/data | 1 Jul update and mature-market inputs |
| S7 | [Damodaran — July 2026 country-risk workbook](https://pages.stern.nyu.edu/~adamodar/pc/datasets/ctrypremJuly26.xlsx) | Primary dataset | Indonesia rating/CDS CRP and total ERP |
| S8 | [Damodaran — risk-free-rate question](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/RiskPrem.htm) | Primary methodology | Double-counting warning |
| S9 | [Damodaran — country-risk question](https://pages.stern.nyu.edu/~adamodar/New_Home_Page/valquestions/CountryRisk.htm) | Primary methodology | Beta/mature ERP/lambda/CRP decomposition |
| S10 | [Damodaran — country-risk valuation slides](https://pages.stern.nyu.edu/~adamodar/pdfiles/country/valintroalldayNew2018.pdf) | Primary methodology | Local risk-free = government yield minus default spread |
| S11 | [ADB AsianBondsOnline — Indonesia](https://asianbondsonline.adb.org/indonesia/) | Official multilateral data | 2y/5y/10y yields and ratings context |
| S12 | [Bank Indonesia — S&P affirmation](https://www.bi.go.id/en/iru/highlight-news/Pages/-S%26P-Affirmed-Indonesia%E2%80%99s-Sovereign-Credit-Rating-at-BBB-with-Stable-Outlook.aspx) | Official issuer summary | 13 Jul BBB/stable affirmation |
| S12b | [HIMDASUN/BNI — fixed-income daily](https://himdasun.or.id/en/2026/07/16/bnis-fixed-income-daily-report-36/) | Secondary market report | 16 Jul 5y CDS quote |
| S13 | [MSCI — 2026 Market Classification Review](https://ir.msci.com/news-releases/news-release-details/msci-announces-results-msci-2026-market-classification-review) | Official primary | Conditional EM retention and November risk |
| S14 | [MSCI — 2026 Global Market Accessibility Review](https://www.msci.com/downloads/web/msci-com/indexes/index-resources/market-classification/MSCI%202026%20GLOBAL%20MARKET%20ACCESSIBILITY%20REVIEW%20REPORT.pdf) | Official primary | Accessibility assessment detail |
| S15 | [Sahm/Reuters — S&P DJI watch](https://www.sahmcapital.com/news/content/update-2-sp-dow-jones-puts-indonesia-on-watch-list-for-market-downgrade-2026-07-08) | Secondary | Separate S&P DJI action |
| S16 | [ANTARA — IDX response to downgrade risk](https://en.antaranews.com/news/421944/idx-pledges-to-defend-emerging-market-status-amid-downgrade-risks) | National newswire | Separate-provider corroboration |
| S17 | [Li, Wei & Zhang — open manuscript](https://ira.lib.polyu.edu.hk/bitstream/10397/118875/1/1-s2.0-S156601412600049X-main.pdf) | Peer-reviewed paper | Actual IDX four-factor construction/results |
| S18 | [DOI 10.1016/j.ememar.2026.101485](https://doi.org/10.1016/j.ememar.2026.101485) | Publisher identifier | Paper identity/publication |
| S19 | [NYU V-Lab — JCI GJR-GARCH](https://vlab.stern.nyu.edu/volatility/VOL.JCI%3AIND-R.GJR-GARCH) | Academic live model | Current asymmetric-volatility estimate |
| S20 | [MARCD, arXiv:2510.10807](https://arxiv.org/html/2510.10807) | Primary preprint | Rolling BIC/three-state precedent |
| S21 | [Regime detection, arXiv:2402.05272](https://arxiv.org/html/2402.05272) | Primary paper/preprint | Two-state HMM vs jump-model evidence |
| S22 | [ID-SMSA dataset V3](https://data.mendeley.com/datasets/tn4vzs8tdw/3) | Official dataset | Dataset scope and DOI |
| S23 | [ID-SMSA dataset paper](https://pubmed.ncbi.nlm.nih.gov/40416745/) | Peer-reviewed paper record | Collection, annotation, scope |
| S24 | [UI research record — IndoBERT on ID-SMSA](https://dara.ui.ac.id/research-output/6fafcf79-d533-468c-a697-1a00de493096) | Institutional paper record | 2025 reported benchmark |
| S25 | [IndoBERT vs mBERT on Indonesian financial news](https://ejournal.uniks.ac.id/index.php/JTOS/article/view/5583) | Peer-reviewed study | Imbalance-aware financial-news metrics |
| S26 | [Current repository checkpoint](https://huggingface.co/mdhugol/indonesia-bert-sentiment-classification/tree/main) | Official model repository | Exact model provenance/domain |
| S27 | [Bailey & López de Prado — Deflated Sharpe Ratio PDF](https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf) | Original paper | DSR methodology |
| S28 | [SSRN 2460551](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551) | Original-paper record | DSR identity/abstract |
| S29 | [Harvey, Liu & Zhu — …and the Cross-Section of Expected Returns](https://academic.oup.com/rfs/article-abstract/29/1/5/1843824) | Peer-reviewed primary | Multiple-testing factor hurdle |
| S30 | [Bailey et al. — Probability of Backtest Overfitting](https://escholarship.org/uc/item/4w1110bb) | Original paper | CSCV/PBO and selection bias |
| S31 | [El-Yaniv & Wiener — Foundations of selective classification](https://jmlr.csail.mit.edu/papers/v11/el-yaniv10a.html) | Peer-reviewed primary | Reject option and risk–coverage |
| S32 | [Guo et al. — Calibration of modern neural networks](https://arxiv.org/abs/1706.04599) | Peer-reviewed/preprint | Held-out calibration/temperature scaling |
| S33 | [Conformal Risk Control](https://arxiv.org/abs/2208.02814) | Peer-reviewed/preprint | Held-out risk-control method and assumptions |
| S34 | [Microsoft — Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/) | Research guidance | Uncertainty/recovery UX |
| S35 | [Federal Reserve — Model Risk Management](https://www.federalreserve.gov/frrs/guidance/supervisory-guidance-on-model-risk-management.htm) | Official supervisory guidance | Effective challenge, benchmarking, pre-set thresholds |
| S36 | [FINRA Notice 15-09](https://www.finra.org/rules-guidance/notices/15-09) | Official industry guidance | Pre-production testing and limited pilots |

## Audit-side GARCH diagnostic note

For triangulation only, this audit fitted normal-QML GJR-GARCH to Yahoo `^JKSE` returns through mid-July. Results were window-dependent:

| Window | n | alpha | beta | gamma | Persistence | AIC asymmetric | AIC symmetric |
|---|---:|---:|---:|---:|---:|---:|---:|
| Full available history | 8,827 | .1143 | .8315 | .0789 | .9853 | 26,977.94 | 27,024.99 |
| Last five years | 1,209 | .0038 | .8233 | .2100 | .9322 | 3,207.23 | 3,232.14 |
| 2023 onward | 836 | .0148 | .8196 | .2263 | .9476 | 2,323.73 | 2,339.44 |

This is an audit diagnostic, not a production estimate: Yahoo data, normal innovations, and no robust standard-error report. Its legitimate conclusion is narrow—leverage asymmetry improves **in-sample fit** in these windows and parameters are not stable enough to hard-code. It does not establish better volatility forecasts; that requires rolling OOS QLIKE/realized-volatility comparison, parameter uncertainty, and a predeclared loss-differential test.
