# Artifact Policy

This project intentionally produces many runtime artifacts. They are useful for
local analysis, historical scoring, audits, and thesis evidence, but they should
not all be tracked in Git by default.

## Runtime State

Keep these files locally during normal operation. They are ignored by Git:

| Path | Purpose |
| --- | --- |
| `output/full_batch_results.json` | Snapshot of the latest batch only. This is the authority for the latest run. |
| `output/merged_batch_results.json` | Latest known state per ticker across runs for dashboard/history use. |
| `output/TOP_3_SWING_TRADES.md` | Latest human-readable swing-trade report. |
| `output/debates/{TICKER}/v{timestamp}/{TICKER}_debate.json` | Versioned local debate history used by `core/historical_scorer.py`. |
| `output/debates/{TICKER}/latest_debate.json` | Latest debate snapshot for explainability, validation, and RAG evidence tools. |
| `output/debates/{TICKER}_debate.json` | Legacy flat debate file kept for compatibility. |
| `output/backtest/backtest_memory.jsonl` | Append-only realized trade outcome memory. |
| `output/telemetry/*` | Runtime telemetry reports and JSONL logs. |
| `output/audit/*` | Runtime audit logs. |
| `output/rag_evidence/*`, `output/ledger/*`, `output/observations/*`, `output/planner/*` | Local operational traces and evidence stores. |

The important distinction is that ignored does not mean disposable. Some ignored
files are local state that the system reads on later runs. Back them up outside
Git when they become important to your research workflow.

## Tracked Artifacts

Only commit artifacts when they are intentionally curated, small enough to
review, and useful as stable evidence. Prefer one of these locations:

| Path | Use |
| --- | --- |
| `docs/sample_outputs/` | Small examples for README or documentation. |
| `reports/frozen_runs/` | Reproducible thesis/demo snapshots for a named run. |
| `data/debate_history_seed/` | Curated seed history if the project needs a portable starting dataset. |

Every curated run should include enough context to reproduce or interpret it:
date, command, mode, tickers, provider limitations, and whether the run used
mock/dry-run data or live providers.

## Git Cleanup

The `.gitignore` policy now ignores active `output/` artifacts while preserving
placeholder files for `output/`, `output/debates/`, and `output/backtest/`.

Because many output files were already tracked before this policy existed,
`.gitignore` alone will not remove them from Git. To stop tracking generated
artifacts while keeping local files on disk, run:

```powershell
git rm -r --cached output
git add .gitignore output/.gitkeep output/debates/.gitkeep output/backtest/.gitkeep
```

Use that command only when you are ready for the next commit to remove generated
runtime artifacts from the repository index.
