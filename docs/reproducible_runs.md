# Reproducible Runs

Use isolated output directories for validation, demos, and thesis snapshots.
This keeps the active `output/` runtime state clean.

## Fast Smoke Run

This dry run avoids live LLM/provider calls and writes to `tmp/`:

```powershell
.\scripts\smoke_orchestrator_temp.ps1
```

The script creates a timestamped directory under `tmp/` and runs:

```powershell
uv run python orchestrator.py --dry-run --no-interactive --skip-scraping --output-dir <tmp-dir>
```

## Live Snapshot Run

For a curated run that you may want to review later:

```powershell
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
uv run python orchestrator.py --no-interactive --skip-scraping --tickers ADRO TLKM WIIM --output-dir "reports/frozen_runs/$stamp"
```

After the run, inspect these files together:

| File | Why it matters |
| --- | --- |
| `full_batch_results.json` | Source of truth for ticker status, verdicts, risk governor, and sizing data. |
| `TOP_3_SWING_TRADES.md` | User-facing report that should match the JSON semantics. |
| `debates/{TICKER}/latest_debate.json` | Per-ticker evidence and debate provenance. |
| `telemetry/latest_batch_report.txt` | Runtime health, call counts, and provider status. |

## Standard Verification

Run the standard local checks with:

```powershell
.\scripts\verify.ps1
```

The default Ruff target focuses on the actively maintained pipeline surface
(`orchestrator.py`, `run_debate.py`, `run_quant_filter.py`, `core/`,
`services/`, and `tests/`). To audit the entire repository, including older
legacy modules, run:

```powershell
.\scripts\verify.ps1 -FullRuff
```

For report/artifact consistency investigations, prefer temp output directories
unless the goal is explicitly to refresh the active `output/` tree.
