# IDX Fundamental Analysis

Python tooling for Indonesian stock research, swing-trade screening, and multi-agent debate.

This repository combines:

- data acquisition from IDX, Stockbit, yfinance, and Google Drive
- ETL and database persistence
- quantitative filtering for swing candidates
- multi-agent debate and CIO verdict generation
- rank aggregation, historical scoring, and markdown reporting through the flagship orchestrator
- FastAPI endpoints and a minimal SvelteKit UI

The project is research tooling, not financial advice.

## Orchestrator

`orchestrator.py` is the flagship execution path of the repository. It turns the
latest quant screen into ranked swing-trade recommendations in a single,
reproducible pipeline run.

### What it does

- validates dependencies and candidate freshness before consuming API budget
- applies regime-aware thresholds before debate execution begins
- runs debates with bounded concurrency and rate limiting
- protects the Gemini budget and aborts cleanly when limits are hit
- applies historical scoring adjustments before final ranking
- writes both machine-readable JSON and human-readable markdown reports
- preserves versioned debate snapshots for auditability and replay
- exposes a Rich-powered terminal experience for interactive runs
- supports headless execution for automation, CI, or scheduled runs

### Output artifacts

- `output/full_batch_results.json`
- `output/TOP_3_SWING_TRADES.md`
- `output/debates/<TICKER>/vYYYYMMDD_HHMMSS/`
- `output/debates/<TICKER>/latest_debate.json`

### Recommended usage

```bash
uv run python orchestrator.py
```

Use `--no-interactive` for unattended runs, `--skip-scraping` when
`top10_candidates.json` is already available, `--dry-run` to validate the
pipeline without live LLM calls, and `--output-dir` to isolate artifacts.

## Architecture

The repo is organized as a pipeline:

`providers/` -> `builders/` -> `db/` and `repositories/` -> `services/` and `core/` -> `output/`

High level responsibilities:

| Layer | Main modules | Responsibility |
| --- | --- | --- |
| Data sources | `providers/` | IDX scraping, Stockbit API, yfinance, Gemini |
| ETL | `builders/` | Build datasets, enrich rows, persist structured outputs |
| Persistence | `db/`, `repositories/` | SQLAlchemy models and query layer |
| Domain services | `services/` | Debate chamber, fair value, API clients, token handling |
| System logic | `core/` | Settings, budget, regime detection, historical scoring, quant filter |
| API | `app/api/` | FastAPI routers and dependency injection |
| UI | `app/ui/` | Minimal SvelteKit client for local web access |
| Artifacts | `output/`, `scratch/` | Generated reports, candidate lists, debate snapshots, temp files |

## Entry Points

| Command | Purpose |
| --- | --- |
| `uv run python main.py -f -o excel` | Run the core IDX ETL, fetch fundamentals, and export to Excel |
| `uv run python main.py -f -o spreadsheet` | Same pipeline, but export to Google Sheets |
| `uv run python build_sector_cache.py` | Build or refresh `output/sector_cache.json` |
| `uv run python run_quant_filter.py` | Run the quantitative swing filter and write `output/top10_candidates.json` plus `scratch/report.md` |
| `uv run python run_debate.py --tickers BBRI BBCA TLKM` | Run the debate chamber for selected tickers and save per-ticker debate JSON |
| `uv run python orchestrator.py` | Flagship end-to-end pipeline: quant screening, debate orchestration, historical scoring, and final swing-trade reporting |
| `uv run python run_api.py` | Start the FastAPI backend on `http://127.0.0.1:8000` |
| `cd app/ui && bun install && bun run dev --open` | Start the local SvelteKit UI |

The orchestrator has a Rich-based terminal UI. It supports:

- `--no-interactive`
- `--skip-scraping`
- `--dry-run`
- `--output-dir`

## Quick Start

### 1. Install prerequisites

- Python 3.12 or newer
- UV package manager
- Chrome or Chromium for IDX scraping
- Bun if you want to run the SvelteKit UI

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and fill in the values that apply to your setup.

Key settings live in `core/settings.py` and `orchestrator.py`.

### 4. Prepare optional sector cache

```bash
uv run python build_sector_cache.py
```

This improves sector resolution for the quantitative filter. It is optional, but recommended.

## Environment Variables

The most important variables are:

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Required for real debate runs |
| `GEMINI_FLASH_MODEL` | Model used for lighter debate steps |
| `GEMINI_PRO_MODEL` | Model used for final CIO reasoning |
| `GOOGLE_SERVICE_ACCOUNT` | JSON credential for Google Sheets / Drive integration |
| `GOOGLE_DRIVE_EMAILS` | Email list used when sharing spreadsheet output |
| `DATABASE_TYPE` | `sqlite` or `postgresql` |
| `DATABASE_HOST` / `DATABASE_PORT` / `DATABASE_USER` / `DATABASE_PASSWORD` | Database connection settings |
| `LOG_LEVEL` | Console log level |
| `LOG_APP_FILENAME` | Main application log file |
| `OUTPUT_DIR` | Root output directory used by the orchestrator |
| `MAX_CONCURRENT_DEBATES` | Parallel debate limit |
| `GEMINI_RPM_LIMIT` | Gemini requests-per-minute cap |
| `BATCH_DELAY_SECONDS` | Delay between debate tasks |
| `TOP_N_SELECTION` | Number of top candidates to keep |
| `MAX_PRICE_RETRY_ATTEMPTS` | Price fetch retry limit |
| `CANDIDATES_MAX_AGE_HOURS` | How long `top10_candidates.json` stays valid |
| `CANDIDATES_AUTO_RERUN` | Auto-run quant filter if candidates are stale |
| `CONVICTION_WEIGHT_CONFIDENCE` | Weight for CIO confidence in conviction scoring |
| `CONVICTION_WEIGHT_RR_RATIO` | Weight for risk/reward in conviction scoring |
| `CONVICTION_RR_NORMALIZATION_CAP` | Cap used when normalizing R/R |
| `PORTFOLIO_MAX_PER_SECTOR` | Sector diversification limit |
| `PORTFOLIO_MIN_CONVICTION` | Minimum score required for selection |

If you only need the default local setup, start with the values in `.env.example` and add Gemini, Google, and database settings as needed.

## Common Workflows

### 1. Build the base dataset

```bash
uv run python main.py -f -o excel
```

Use `-o spreadsheet` if you want Google Sheets output instead of a local Excel file.

### 2. Run the quant filter

```bash
uv run python run_quant_filter.py
```

Outputs:

- `output/top10_candidates.json`
- `scratch/report.md`

The filter reads the latest Excel workbook in `output/` by default.

### 3. Run a standalone debate batch

```bash
uv run python run_debate.py --tickers BBRI BBCA TLKM
```

Outputs:

- `output/debates/<TICKER>_debate.json`

The standalone debate wrapper keeps the classic flat file layout. The versioned
per-ticker snapshot layout is produced by `orchestrator.py` for auditability and
historical scoring.

### 4. Run the full orchestrator

```bash
uv run python orchestrator.py
```

What it does:

1. validates dependencies
2. checks the candidate file freshness
3. detects market regime
4. runs debates with bounded concurrency
5. ranks candidates with historical adjustments
6. writes `output/full_batch_results.json`
7. writes `output/TOP_3_SWING_TRADES.md`
8. writes versioned debate snapshots under `output/debates/<TICKER>/`

If you omit `--no-interactive`, the orchestrator shows a Rich terminal flow with progress and summary panels.

### 5. Start the API

```bash
uv run python run_api.py
```

Useful URLs:

- `http://127.0.0.1:8000/docs`
- `http://127.0.0.1:8000/redoc`
- `http://127.0.0.1:8000/api/v1/health`

API routers currently cover:

- health
- stocks
- fundamentals
- sentiments
- key analysis
- stock prices

### 6. Start the UI

```bash
cd app/ui
bun install
bun run dev --open
```

`run_ui.sh` is a convenience wrapper for Unix-like shells. It assumes `nvm` and Bun are available.

## Output Directory

The main generated artifacts are:

- `output/top10_candidates.json`
- `scratch/report.md`
- `output/full_batch_results.json`
- `output/TOP_3_SWING_TRADES.md`
- `output/debates/`
- `output/sector_cache.json`

Treat files in `output/` as generated artifacts. They can be regenerated from the pipeline.

## Project Layout

- `providers/` - external data adapters
- `builders/` - ETL and persistence builders
- `repositories/` - SQLAlchemy repository layer
- `services/` - debate, valuation, Google Drive, and API helpers
- `core/` - settings, quant filter, regime, budget, and historical scoring
- `db/` - models and session management
- `schemas/` - Pydantic models and debate schemas
- `app/api/` - FastAPI app and routers
- `app/ui/` - SvelteKit frontend
- `tests/` - pytest suite
- `output/` - generated reports and snapshots
- `scratch/` - temporary analysis and experimental files

## Testing

Run the full test and lint pass with:

```bash
uv run python -m py_compile orchestrator.py run_quant_filter.py services/debate_chamber.py
uv run ruff check .
uv run pytest -q
```

The current test suite focuses on:

- debate chamber reliability
- dependency validation
- historical scoring
- regime detection
- portfolio optimization

## Troubleshooting

- If debate runs fail immediately, check `GEMINI_API_KEY` and the configured Gemini model names.
- If the orchestrator says candidates are stale, rerun `run_quant_filter.py` or let auto-rerun handle it.
- If IDX scraping fails, make sure Chrome or Chromium is installed.
- If the UI does not start, install Bun and run `bun install` inside `app/ui`.

## Notes

- The historical scorer reads debate snapshots from `output/debates/`.
- The debate pipeline now stores versioned per-ticker snapshots plus a latest file for compatibility.
- `core/quant_filter/` is the canonical implementation of the swing candidate filter; `run_quant_filter.py` is only a wrapper.
- `orchestrator.py` is the main end-to-end swing-trade pipeline entry point.

## License

MIT License. See `LICENSE` if present in your checkout.
