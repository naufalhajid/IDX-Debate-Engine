# Backtest Reporting Standard — IDX Debate Chamber

## Significance Threshold

| Metric | Threshold | Interpretation |
|--------|-----------|----------------|
| Deflated Sharpe Ratio (DSR) | > 0.95 | Statistically significant — may deploy |
| Deflated Sharpe Ratio (DSR) | 0.50 – 0.95 | Inconclusive — extend track record |
| Deflated Sharpe Ratio (DSR) | < 0.50 | Not significant — do **not** deploy |

**DSR ≥ 0.95 is required before deploying any strategy.**

## Background

All Sharpe Ratio results in this project are reported as **Deflated Sharpe Ratio (DSR)**,
following Bailey & Lopez de Prado (2014). The standard "annualised SR" overstates
significance whenever multiple parameter combinations have been tried — each additional
trial raises the probability of a lucky false positive.

DSR corrects for this by:
1. Computing the **Probabilistic SR (PSR)** — the probability the true SR exceeds a
   benchmark (0.5 annualised for IDX swing strategies).
2. Deflating PSR by the **expected maximum SR** achievable over `n_trials` random
   parameter sweeps (Gumbel approximation, Bailey-LdP eq. 10).

When `n_trials = 1` (single strategy, no grid search), DSR equals PSR.

Reference: Bailey, D.H. & Lopez de Prado, M. (2014).
*"The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
Non-Normality."* SSRN: 2460551.

## Walk-Forward Validation Protocol

- **In-sample window**: 252 trading days (≈ 1 year)
- **Out-of-sample (OOS) window**: 63 trading days (≈ 1 quarter)
- **Minimum windows required**: 4 (provides ≈ 4× degrees of freedom for statistical
  reliability)
- **Splits**: Strict non-overlapping — no data from an OOS window ever feeds calibration

The aggregate DSR across all OOS windows uses `n_trials = n_windows` to account for
the fact that each window represents one implicit parameter trial.

## IDX Transaction Cost Model

Every backtest applies realistic one-way costs before counting a trade as profitable:

| Component | Rate | Notes |
|-----------|------|-------|
| Broker commission | 0.19% | Competitive online broker; use 0.28% for conventional |
| Pajak penjualan | 0.10% | Sell-only; 0% on buy |
| Bid-ask spread | ½ tick | Tier-based per IDX fractional tick rules |

**IDX tick-size tiers (Kep-00002/BEI/04-2025)**:

| Price range (IDR) | Tick size |
|-------------------|-----------|
| ≤ 200 | 1 |
| 201 – 500 | 2 |
| 501 – 2,000 | 5 |
| 2,001 – 5,000 | 10 |
| > 5,000 | 25 |

## Survivorship Bias Awareness

All backtests **must** specify the ticker universe and its vintage date. Inclusion of
delisted or suspended stocks in the universe introduces look-ahead bias. The
`scripts/run_backtest.py` tool records the universe at run time in the output report.

For production backtests covering > 3 years, use a point-in-time universe snapshot
(e.g., IDX80 membership as of each rebalance date) rather than today's constituents.

## Running a Backtest

```bash
# Single ticker, 3-year history
uv run python scripts/run_backtest.py --tickers BBRI --years 3

# Multiple tickers (comma-separated)
uv run python scripts/run_backtest.py --tickers BBRI,BBCA,TLKM --years 5

# Custom windows
uv run python scripts/run_backtest.py --tickers BMRI --insample 252 --oos 63
```

Output is written to `docs/backtest_results/YYYY-MM-DD.md`.

## Interpreting the Report

```
Ticker   Sharpe   DSR(n=W)   Windows   Result
──────   ──────   ─────────  ───────   ──────
BBRI     1.24     0.97       6         SIGNIFICANT ✓
BBCA     0.68     0.71       5         inconclusive
TLKM     0.31     0.22       4         NOT SIGNIFICANT ✗
```

- **Sharpe**: Classic annualised SR — shown for reference only; do not use for
  go/no-go decisions.
- **DSR(n=W)**: Deflated SR with `n_trials = W` (number of OOS windows). Use this
  for deployment decisions.
- **Significant**: DSR > 0.95. Below 0.95, do not deploy regardless of SR.
