# Fundamental Recalibration Log (IDX4-Inspired Characteristics)

> Terminology correction (2026-07): this repository uses stock-level
> OCF/Price and RNOA/ROA scoring inspired by the published IDX research. It
> does **not** implement or validate the paper's factor-mimicking portfolios.
> See `RESEARCH_LEDGER_2026-07.md` for the independently verified evidence.

**Date:** 2026-06-23  
**Scope:** IDX Debate Chamber fundamental, valuation, and Bull/Bear prompt calibration  
**Implementation note:** The requested path `src/config/idx_market_params.py` was adapted to
`core/idx_market_params.py` because this repository uses top-level packages (`core`,
`services`, `providers`) rather than a `src/` layout.

## Parameter Changes

| Parameter | Changed From | Changed To | Reason and Citation |
|---|---:|---:|---|
| Indonesia Total ERP | 9.23% template / prior stale default | 7.5501% | Damodaran Online Country Risk Premiums workbook, Indonesia row, Apr 1 2026 update. Uses rating-based Total Equity Risk Premium. The older 9.23% was a stale/high template and would overstate `Ke = Rf + beta * ERP`. |
| Indonesia Country Risk Premium | 3.7% template | 2.7801% | Damodaran Online Country Risk Premiums workbook, Indonesia row, Apr 1 2026 update. |
| Mature Market Premium | 5.5% template | 4.77% | Damodaran Online Country Risk Premiums workbook, Apr 1 2026 country ERP table. |
| Indonesia Risk-Free Fallback | 6.5% template | 7.14% | Existing code already used the local June 2026 SBN 10Y fallback, and `services/macro_refresh.py` can override it from cache. No unverified downgrade to 6.5% was made. |
| Bull/value scoring emphasis | `weight_valuation = 20` | `weight_valuation = 40` | IDX4 Four-Factor Model brief: OCF/Price is the preferred IDX value proxy over P/B/book-to-market. |
| Bull/quality scoring emphasis | `weight_profitability = 10` | `weight_profitability = 30` | IDX4 Four-Factor Model brief: RNOA outperforms ROE/operating profitability as an Indonesian profitability factor. |
| Bull/momentum scoring emphasis | RSI 25, volume 25, price momentum 20 (70 total technical/momentum points) | RSI 15, volume 8, price momentum 7 (30 total technical/momentum points) | ICMR 2024 Industry Momentum on IDX: positive industry momentum alpha reported but not statistically significant, so momentum is supporting evidence, not the dominant vote. |
| Non-financial value signal | Graham gap plus PE blend only | OCF/Price primary blend plus Graham/PE fallback | IDX4 Four-Factor Model brief: OCF/Price was selected as the better value proxy for IDX than P/B. Financial sectors keep PBV-relative scoring because bank/finance operating cash flow is not comparable to non-financial companies. |
| Profitability signal | ROE-only tier score | RNOA/ROA proxy 70% plus ROE 30% | IDX4 Four-Factor Model brief: RNOA is preferred; ROA is used when RNOA is unavailable. ROE remains a secondary signal to preserve continuity. |
| ARA session boundary | `<200: 35%, <=5000: 25%, >5000: 20%` | `<=50: 35%, <=200: 25%, >200: 20%` | BEI Kep-00002/BEI/04-2025, effective Apr 8 2025. Fixes undercounting ARA sessions for Rp51-199 and Rp201-5000 names. |
| Tick size constants | Hardcoded inside `snap_to_tick()` | Centralized in `core/idx_market_params.py` | IDX fraksi harga constants should share one source with execution/risk logic. |
| Lot size | Hardcoded `LOT_SIZE = 100` in position sizing | Centralized `LOT_SIZE = 100` | IDX trading constraint: 1 lot = 100 shares. |

## Files Updated

| Area | File | Change |
|---|---|---|
| Constants | `core/idx_market_params.py` | Added IDX ERP, CRP, risk-free fallback, ARA/ARB, tick, lot, settlement, and factor weight constants. |
| Factor helpers | `core/fundamental_factors.py` | Added `calculate_ocf_price_ratio()`, `calculate_rnoa()`, and `calculate_profitability_score()`. |
| Settings | `core/settings.py` | Default `IDX_ERP` and `SBN_10Y_YIELD` now reference the centralized IDX market constants. |
| Screener config | `core/quant_filter/config.py` | Reweighted scoring toward value and quality; added OCF/Price and RNOA thresholds. |
| Screener pipeline | `core/quant_filter/pipeline.py` | Added OCF/Price extraction, RNOA/ROA proxy extraction, value score blend, and quality score blend. |
| Fair value | `services/fair_value_calculator.py` | Parses OCF, shares outstanding, ROA/RNOA, exposes OCF/Price and profitability proxy to payload and report. |
| Prompt context | `services/context_pack_builder.py` | Adds OCF/Price, RNOA/ROA, and quality factor fields to the debate prompt surface. |
| Debate prompts | `services/debate_prompts/*.txt` | Bull/Bear/Fundamental now prefer value/quality evidence over momentum-only arguments. |
| Risk governor | `core/risk_governor.py` | Uses centralized ARA boundary helper. |
| Execution utilities | `utils/technicals.py`, `core/quant_filter/position_sizer.py` | Uses centralized tick size and lot size constants. |

## Source Notes

- Damodaran data conflict resolved by live verification: the older gap report cited a Jan 2026 Indonesia ERP of 6.69%, while the current Damodaran spreadsheet available on 2026-06-23 showed Indonesia Total Equity Risk Premium of 7.5501% in the Apr 1 2026 workbook. The implementation uses the verified current workbook value and records the update date in constants.
- The brief requested `INDONESIA_TOTAL_ERP = 0.0923`; this was intentionally not used because the same brief instructed checking Damodaran's latest website first.
- OCF/Price is treated as primary for non-financial companies only. For banks and other financials, PBV remains the safer sector-relative proxy because operating cash flow is structurally different for balance-sheet businesses.

## Validation Checklist

- Unit tests added for OCF/Price helper, RNOA/ROA profitability scoring, OCF/Price value scoring, fair-value payload/report exposure, context-pack rendering, and ARA tier boundaries.
- Expected value changes in screener tests are documented inline where formula weights changed.
- Focused verification should run:

```bash
uv run pytest tests/test_quant_filter_pipeline.py tests/test_fair_value_calculator.py tests/test_ara_arb_regression.py -q
```
