# IDX Fundamental Analysis

CLI-first research engine for Indonesian stock analysis, swing-trade screening, and AI-assisted investment reasoning.

This project was built to answer one practical question:

> From many IDX stocks, which names deserve deeper attention for a short-to-medium term swing trade, and why?

The system combines market data collection, quantitative filtering, multi-agent AI debate, risk/reward scoring, portfolio diversification, and markdown reporting into one reproducible command-line pipeline.

This is research tooling, not financial advice.

## Why This Project Exists

Retail investors often face too much scattered information: price movement, valuation metrics, technical levels, market sentiment, sector context, and news catalysts all compete for attention. This project turns that messy workflow into a structured pipeline.

Instead of manually checking every stock, the CLI:

- collects and prepares stock data from multiple sources
- filters candidates using quantitative rules
- sends selected tickers into a multi-agent debate process
- scores each result using confidence, risk/reward, and historical behavior
- produces a readable final report with trade levels, risks, and rationale

The project is intentionally CLI-first. The priority is the analysis engine: correctness, reproducibility, automation, logs, and useful output artifacts.

## Core Showcase

`orchestrator.py` is the main showcase file. It runs the end-to-end swing-trade pipeline:

```text
Quant Filter -> Pre-CIO Risk Filter -> Market Regime Detection
             -> Multi-Agent Debate -> Conviction Scoring
             -> Portfolio Diversification -> Position Sizing
             -> Markdown + JSON Reports
```

Recommended command:

```bash
uv run python orchestrator.py
```

For unattended runs:

```bash
uv run python orchestrator.py --no-interactive --skip-scraping
```

For validating the pipeline without live Gemini calls:

```bash
uv run python orchestrator.py --dry-run
```

## Orchestrator Analysis

The orchestrator is designed as a production-style CLI pipeline rather than a simple script.

| Area | What it does |
| --- | --- |
| Pre-flight validation | Checks required dependencies, output folders, candidate freshness, and Gemini availability before spending API budget |
| Market regime awareness | Fetches IHSG volatility, classifies regime, and adjusts thresholds such as top-N selection and conviction limits |
| Candidate parsing | Reads `output/top10_candidates.json`, validates IDX tickers, removes duplicates, and skips critical-risk entries |
| Pre-CIO filtering | Skips candidates with near ExDate risk and avoids counter-trend setups in high-volatility regimes |
| Rate limiting | Uses `SafeRateLimiter` with a monotonic clock and lock-safe sliding window logic |
| Concurrency control | Runs multiple debates with `asyncio.Semaphore` while respecting Gemini request limits |
| Budget protection | Uses abort flags and budget charging so expensive LLM calls stop cleanly when the budget is exhausted |
| Debate execution | Calls `DebateChamber` for each ticker and normalizes the final verdict, debate history, and agent votes |
| Scoring | Computes conviction score from CIO confidence and normalized risk/reward ratio |
| Historical adjustment | Reads previous debate records and adjusts scoring based on historical win-rate behavior |
| Portfolio diversification | Applies sector caps so the final picks are not concentrated in one sector |
| Position sizing | Calculates suggested allocation using user capital, max loss per trade, and max positions |
| Persistence | Writes full JSON results, versioned per-ticker debate snapshots, and a human-readable markdown report |
| CLI experience | Uses Rich panels, tables, progress bars, and graceful Ctrl+C handling |

This makes `orchestrator.py` the best file to explain in a portfolio interview because it shows system design, async programming, reliability thinking, AI integration, and product judgment in one place.

## Example Output

The main artifacts are generated under `output/`:

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

The final markdown report includes:

- final BUY/STRONG_BUY/HOLD/AVOID rating
- CIO confidence
- conviction score
- entry range
- target price
- stop loss
- risk/reward ratio
- winning argument
- devil's advocate warning
- CIO summary
- position sizing summary

## Features

- IDX stock data scraping and enrichment
- Stockbit and yfinance integration
- Google Drive / Google Sheets export support
- Quantitative swing-trade candidate filtering
- AI-powered multi-agent debate chamber
- CIO-style final verdict generation
- Market regime detection
- Risk/reward and conviction scoring
- Historical scoring adjustment from previous debate records
- Sector-aware portfolio diversification
- Position sizing based on capital and risk limit
- Rich-powered interactive CLI
- JSON and markdown report generation
- Async execution with rate limiting and graceful abort behavior
- Pytest coverage for core reliability modules

## Architecture

```text
providers/      External data sources: IDX, Stockbit, yfinance, Gemini, Google Drive
builders/       ETL and analysis builders
db/             SQLAlchemy models and async session setup
repositories/   Database query layer
services/       Debate chamber, AI assistant, valuation, API clients
core/           Settings, budget, regime detection, quant filter, scoring, optimizer
schemas/        Pydantic models for validation and structured outputs
utils/          Logging, price fetching, Excel helpers, serialization
tests/          Pytest suite
output/         Generated reports and debate snapshots
scratch/        Temporary analysis files and experiments
```

High-level flow:

```text
Data Sources
  -> ETL / Analysis
  -> Quant Filter
  -> Debate Chamber
  -> Scoring + Portfolio Rules
  -> CLI Summary + Output Reports
```

## Tech Stack

| Category | Tools |
| --- | --- |
| Language | Python 3.12+ |
| Package manager | UV |
| Data processing | pandas, numpy, openpyxl |
| Scraping | undetected-chromedriver, BeautifulSoup, requests |
| Database | SQLAlchemy async, SQLite via aiosqlite, optional PostgreSQL config |
| API layer | FastAPI, Uvicorn |
| AI / LLM | Gemini, LangGraph, LangChain |
| Reliability | Tenacity, Loguru, Rich |
| Validation | Pydantic v2 |
| Testing | pytest, pytest-asyncio |
| Code quality | Ruff, isort |

Note: the repository contains API and UI folders, but the main product experience for this showcase is the CLI analysis pipeline.

## Quick Start

### 1. Install prerequisites

- Python 3.12 or newer
- UV package manager
- Chrome or Chromium for IDX scraping
- Gemini API key for real debate runs

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment

Copy `.env.example` to `.env` and fill the values you need:

```bash
GEMINI_API_KEY=your_key_here
LOG_LEVEL=INFO
```

Most runtime settings live in `core/settings.py`.

### 4. Build optional sector cache

```bash
uv run python build_sector_cache.py
```

### 5. Run the full pipeline

```bash
uv run python orchestrator.py
```

## Common CLI Workflows

Build the base dataset:

```bash
uv run python main.py -f -o excel
```

Run the quantitative candidate filter:

```bash
uv run python run_quant_filter.py
```

Run debate for selected tickers:

```bash
uv run python run_debate.py --tickers BBRI BBCA TLKM
```

Run the full orchestrator:

```bash
uv run python orchestrator.py
```

Run in headless mode:

```bash
uv run python orchestrator.py --no-interactive --skip-scraping
```

## CLI Options

| Option | Purpose |
| --- | --- |
| `--no-interactive` | Skips Rich banner and prompts for automation |
| `--skip-scraping` | Assumes candidate data already exists |
| `--scrape-cmd` | Uses a custom scraping command before the pipeline starts |
| `--dry-run` | Uses mock debate results without Gemini calls |
| `--output-dir` | Writes artifacts to a custom output directory |

## Important Environment Variables

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for real AI debate runs |
| `GEMINI_FLASH_MODEL` | Model for lighter AI tasks |
| `GEMINI_PRO_MODEL` | Model for deeper debate and CIO reasoning |
| `OUTPUT_DIR` | Root folder for generated artifacts |
| `MAX_CONCURRENT_DEBATES` | Parallel debate limit |
| `GEMINI_RPM_LIMIT` | Gemini request-per-minute cap |
| `BATCH_DELAY_SECONDS` | Delay between debate tasks |
| `TOP_N_SELECTION` | Number of selected final candidates |
| `CANDIDATES_MAX_AGE_HOURS` | Freshness limit for candidate JSON |
| `CANDIDATES_AUTO_RERUN` | Auto-run quant filter when candidates are stale |
| `CONVICTION_WEIGHT_CONFIDENCE` | Weight for CIO confidence |
| `CONVICTION_WEIGHT_RR_RATIO` | Weight for risk/reward |
| `CONVICTION_RR_NORMALIZATION_CAP` | Cap used when normalizing R/R |
| `PORTFOLIO_MAX_PER_SECTOR` | Maximum selected names per sector |
| `PORTFOLIO_MIN_CONVICTION` | Minimum conviction score for final selection |

## Testing

Run syntax checks, linting, and tests:

```bash
uv run python -m py_compile orchestrator.py run_quant_filter.py services/debate_chamber.py
uv run ruff check .
uv run pytest -q
```

Current tests focus on:

- debate chamber reliability
- dependency validation
- historical scoring
- market regime detection
- portfolio optimization

## Portfolio Angle

This project is suitable as a technical portfolio project because it shows:

- ability to turn a real-world problem into an automated system
- async Python and concurrency control
- API budget and rate-limit awareness
- structured AI integration, not just a simple prompt call
- data pipeline design
- risk-based decision support
- readable CLI product experience
- generated artifacts that can be audited and replayed

For Apple Developer Academy, the strongest story is not "I built a stock bot." The stronger story is:

> I built a CLI decision-support engine that helps Indonesian retail investors process complex stock information more systematically, while keeping the reasoning, risks, and assumptions visible.

## Future Improvements

- Add a lightweight SwiftUI dashboard that reads generated JSON reports
- Improve explainability for each scoring component
- Add backtesting for historical recommendations
- Add more robust data quality checks before debate execution
- Create a portfolio summary report across multiple pipeline runs
- Add CI workflows for tests and linting

## Troubleshooting

- If debate runs fail immediately, check `GEMINI_API_KEY` and model settings.
- If candidates are stale, run `uv run python run_quant_filter.py`.
- If IDX scraping fails, make sure Chrome or Chromium is installed.
- If generated reports look empty, check `output/top10_candidates.json` and `pipeline.log`.
- If Gemini budget is exhausted, lower `MAX_CONCURRENT_DEBATES` or reduce the number of candidates.

## License

MIT License. See `LICENSE` if present in your checkout.
