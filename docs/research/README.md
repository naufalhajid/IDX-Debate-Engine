# Research Document Status

This folder contains research notes, diagnostic reports, calibration write-ups,
and model comparisons. These files are useful context, but they are not runtime
contracts for the production pipeline.

Current lifecycle rules:

| Lifecycle | Meaning | Examples in this folder |
| --- | --- | --- |
| Research | Explicit investigation or calibration work | Forecasting reports, signal IC studies, gate diagnostics |
| Historical | Snapshot of a past system state | Gap analyses and recalibration notes that cite old thresholds or old command behavior |
| Advisory | Context that may inform future changes | Fundamental recalibration ideas, sentiment calibration notes |

Before implementing a recommendation from these documents, verify it against:

1. `docs/architecture_decision_map.md`
2. `docs/de_overengineering_execution_checklist_2026-07-03.md`
3. The current runtime code and focused tests

No file in this folder should imply that forecasting, fair value, single-agent
comparison, or backtest recalibration can silently override the default
production `idx pipeline` path.
