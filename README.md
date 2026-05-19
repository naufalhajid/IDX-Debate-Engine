# IDX Fundamental Analysis

CLI-first research engine for Indonesian Stock Exchange (IDX) swing-trade analysis.

The project turns a manual stock-screening workflow into a reproducible pipeline:

```text
Quant scouting -> multi-agent debate -> risk validation
               -> conviction scoring -> portfolio sizing -> reports
```

This is decision-support software for research and portfolio review. It is not financial advice.

## What It Does

`orchestrator.py` is the main entry point. It reads quantitative candidates, runs a structured AI debate for each selected ticker, validates whether a setup is actually actionable, ranks results by conviction, sizes positions, and writes auditable JSON and markdown reports.

The repository is intentionally CLI-first. The strongest supported interface today is the Python command-line pipeline, not a hosted web app.

## Main Outputs

Runtime artifacts are generated under `output/` and are intentionally ignored by git:

```text
output/
  top10_candidates.json
  full_batch_results.json
  TOP_3_SWING_TRADES.md
  debates/
    <TICKER>/
      latest_debate.json
      vYYYYMMDD_HHMMSS/
```

Sanitized demo artifacts live in `examples/`:

- `examples/sample_full_batch_results.json`
- `examples/sample_TOP_3_SWING_TRADES.md`

## Pipeline Flow

| Stage | Purpose |
| --- | --- |
| Quant filter | Builds a ranked candidate list from market and fundamental inputs |
| Candidate intake | Normalizes tickers, rejects malformed records, and handles stale input files |
| Market regime | Uses IHSG volatility context to adjust selection behavior |
| Debate chamber | Runs specialist agents and a CIO-style final verdict |
| Risk governor | Separates deployable buys from watchlist or pullback-only setups |
| Conviction scoring | Combines CIO confidence, risk/reward, and historical/realized outcomes |
| Portfolio rules | Applies sector diversification and position sizing |
| Reporting | Writes JSON, per-ticker debate snapshots, markdown reports, logs, and telemetry |

## Setup

Requirements:

- Python 3.12
- `uv`
- Chrome or Chromium if you run IDX/Stockbit browser scraping
- Gemini API key for real AI debate runs

Install dependencies:

```bash
uv sync
```

Optional experimental crawler support:

```bash
uv sync --extra crawler
```

Create local configuration:

```bash
cp .env.example .env
```

Then edit `.env` locally. Never commit `.env`.

## Environment

Important settings:

| Variable | Required | Purpose |
| --- | --- | --- |
| `GEMINI_API_KEY` | Real mode only | Gemini API key for debate and CIO calls |
| `GEMINI_FLASH_MODEL` | No | Model for lighter LLM tasks |
| `GEMINI_PRO_MODEL` | No | Model for deeper debate/CIO reasoning |
| `DATABASE_TYPE` | No | Must be `sqlite`; other engines are not implemented |
| `DATABASE_PATH` | No | SQLite database path, default `db/idx-fundamental.db` |
| `CANDIDATES_MAX_AGE_HOURS` | No | Freshness window for `top10_candidates.json` |
| `CANDIDATES_AUTO_RERUN` | No | Whether stale candidates trigger quant-filter rerun |
| `CONVICTION_WEIGHT_CONFIDENCE` | No | Conviction weight for CIO confidence |
| `CONVICTION_WEIGHT_RR_RATIO` | No | Conviction weight for risk/reward |
| `PORTFOLIO_MAX_PER_SECTOR` | No | Sector concentration cap |
| `PORTFOLIO_MIN_CONVICTION` | No | Minimum score for final selection |
| `LOG_LEVEL` | No | Console logging level |

SQLite is the supported database backend in the current codebase. PostgreSQL settings are not a supported runtime path.

## Common Commands

Run the full pipeline:

```bash
uv run python orchestrator.py
```

Run without prompts and use existing candidate data:

```bash
uv run python orchestrator.py --no-interactive --skip-scraping
```

Run dry mode without live Gemini calls:

```bash
uv run python orchestrator.py --dry-run --no-interactive --output-dir tmp/orchestrator_dry_run
```

Build or refresh quantitative candidates:

```bash
uv run python run_quant_filter.py
```

Run debate for selected tickers:

```bash
uv run python run_debate.py --tickers BBRI BBCA TLKM --output-dir output/debates
```

Run the older ETL/export flow:

```bash
uv run python main.py -f -o excel
```

## Interpreting Reports

`TOP_3_SWING_TRADES.md` is the human-readable summary. It includes final rating, CIO confidence, conviction score, entry range, target, stop loss, risk/reward, actionability, sizing eligibility, thesis, catalysts, and risks.

`full_batch_results.json` is the machine-readable batch artifact. It is better for audits, dashboards, and comparing runs over time.

Important semantics:

- `BUY` does not always mean "buy immediately"; check `risk_governor.status`.
- `deployable` means the setup passed risk validation and can be sized.
- `wait_for_pullback` means the thesis can remain visible, but sizing is blocked.
- Failed tickers should be treated as failed analysis, not as bearish verdicts.

## Tests And Quality Checks

Recommended verification:

```bash
uv sync
uv run python -m compileall -q .
uv run ruff check .
uv run pytest -q
```

For a focused check during development:

```bash
uv run pytest tests/test_report_consistency.py -q
uv run ruff check core/report_consistency.py
```

## Repository Hygiene

The repo ignores local secrets, caches, database files, logs, spreadsheets, CSV exports, and generated runtime output. Keep only sanitized examples in `examples/`.

Do not commit:

- `.env` or `.env.*`
- `output/`
- `logs/`
- `tmp/`
- `pipeline.log`
- local SQLite databases
- generated spreadsheets or CSV exports

## Current Limitations

- The supported runtime database is SQLite.
- Real debate mode depends on external provider availability and API quota.
- IDX and Stockbit scraping may require browser access and can be affected by website changes.
- The crawler provider is optional and experimental.
- The project is CLI-first; a web dashboard can be built later from generated JSON artifacts.

## Why This Is Portfolio-Ready

The project demonstrates practical software engineering around a real workflow: async orchestration, provider boundaries, rate limits, budget awareness, structured AI reasoning, risk controls, artifact validation, reproducible CLI usage, and tests around core behavior.

The important engineering story is not "an AI stock picker." It is a transparent decision-support pipeline that keeps assumptions, risks, actionability, and generated artifacts inspectable.
