---
name: idx-analysis-pipeline
description: Run, debug, and modify the IDX Fundamental Analysis data and ranking pipeline. Use when Codex is asked about orchestrator.py, main.py ETL, quant filtering, output files, sector cache, candidate selection, batch debates, ranking, report generation, or failures around output/top10_candidates.json and output/TOP_3_SWING_TRADES.md.
---

# IDX Analysis Pipeline

## Orientation

Use this skill for end-to-end project workflows: data collection, Excel/database output, quantitative filtering, debate batches, final ranking, and report generation. Prefer changing the smallest stage that owns the issue instead of patching symptoms in later stages.

## Pipeline Map

1. `main.py`: fetch IDX stock list, enrich with StockBit price/fundamental/stream data, build Excel or spreadsheet output, and populate the local SQLite database.
2. `build_sector_cache.py`: precompute sector classification data used by filtering and reports.
3. `run_quant_filter.py`: run `core.quant_filter.pipeline.run_pipeline(CONFIG)` and write `output/top10_candidates.json`.
4. `orchestrator.py`: load candidates, optionally validate freshness, run debate batches, rank conviction, persist debate JSON, and write `output/TOP_3_SWING_TRADES.md`.

## Common Commands

Refresh source data and generate an Excel file:

```bash
uv run python main.py -f -o excel
```

Build or refresh sector cache:

```bash
uv run python build_sector_cache.py
```

Run the quant filter:

```bash
uv run python run_quant_filter.py
```

Run the full orchestrator:

```bash
uv run python orchestrator.py --output-dir output
```

Use `--dry-run` on `orchestrator.py` when you need dependency and candidate checks without spending LLM calls.

## Files To Inspect By Symptom

- Missing or stale `top10_candidates.json`: inspect `core/dependency_validator.py`, `core/quant_filter/config.py`, and `core/quant_filter/pipeline.py`.
- Candidate scoring looks wrong: inspect scoring stages in `core/quant_filter/pipeline.py`, position sizing in `core/quant_filter/position_sizer.py`, and report helpers in `core/quant_filter/reporting.py`.
- Sector labels or PBV benchmarks look wrong: inspect `core/quant_filter/config.py` and `output/sector_cache.json`.
- Batch debates fail or skip tickers: inspect `orchestrator.py` around candidate loading, debate task execution, rate limits, and retry handling.
- Final top-3 report is empty: inspect rating exclusions, minimum conviction, regime overrides, and output normalization in `orchestrator.py`.

## Modification Rules

Keep stage boundaries clear:

- Put candidate filtering and quant-derived columns in `core/quant_filter/*`.
- Put batch orchestration, LLM debate execution, ranking, and final report decisions in `orchestrator.py`.
- Put market regime thresholds in `core/regime.py` or settings/env wiring.
- Put API cost/rate concerns in `core/budget.py` and settings.

Prefer additive output changes. If you rename a field in `top10_candidates.json` or debate JSON, update all readers in `orchestrator.py`, reports, tests, and historical scoring compatibility code.

## Validation

For pure quant/report changes:

```bash
uv run pytest tests/test_regime.py tests/test_portfolio_optimizer.py tests/test_historical_scorer.py
uv run ruff check .
```

For dependency validation changes:

```bash
uv run pytest tests/test_dependency_validator.py
```

For changes that touch live providers or Gemini, separate deterministic unit tests from optional live runs. Do not assume network/API credentials are available unless the user asks for a live run.

## Operational Cautions

- Do not read, print, or commit secrets from `.env`.
- Treat generated files under `output/` as artifacts unless the user asks to update committed outputs.
- Be careful with reruns: `main.py`, `run_quant_filter.py`, and `orchestrator.py` can overwrite outputs.
- If an error mentions Chrome/Selenium/IDX scraping, check provider setup before changing analysis logic.
