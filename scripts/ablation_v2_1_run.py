"""scripts/ablation_v2_1_run.py — V2.1 live leg: debate vs single-agent, same 25 tickers.

Runs `idx pipeline compare` in-process for a fixed cross-sector 25-ticker universe
drawn from tickers this pipeline has previously debated (today's real quant filter
run cleared only 1/957 under the live DEFENSIVE regime — too thin a sample on its
own, see output/top10_candidates.json for that leg instead).

Sandboxed away from the production backtest ledger / observation store / watchlist
log under output/ablation_v2_1/ so this ablation-motivated ticker selection does
not pad the organic track record V3.1/V5 read from (output/backtest/*.jsonl,
output/observations/observations.jsonl stay untouched). evaluate_memory is
stubbed out entirely rather than redirected: its memory_path default is a plain
Path bound at def-time to the real ledger, so leaving it live would evaluate (and
persist, write=True) real open trades as an unrelated side effect of this run.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from utils.ticker import normalize_idx_tickers

TICKERS: list[str] = [
    "BBCA",
    "BBRI",
    "BBNI",
    "BMRI",
    "ICBP",
    "UNVR",
    "MYOR",
    "GGRM",
    "ULTJ",
    "ERAA",
    "MAPI",
    "ACES",
    "AMRT",
    "MIDI",
    "ADRO",
    "PTBA",
    "ITMG",
    "ANTM",
    "TINS",
    "BRPT",
    "TPIA",
    "CUAN",
    "TLKM",
    "CPIN",
    "BREN",
]

ABLATION_DIR = Path("output/ablation_v2_1")


def _sandbox_shared_state() -> None:
    import core.orchestrator.legacy as legacy
    from core.backtest_outcome_evaluator import EvaluationSummary
    from core.observation_store import DEFAULT_STORE

    legacy.DEFAULT_MEMORY.path = ABLATION_DIR / "backtest" / "backtest_memory.jsonl"
    legacy._WATCHLIST_LOG_PATH = ABLATION_DIR / "backtest" / "watchlist_log.jsonl"
    DEFAULT_STORE.path = ABLATION_DIR / "observations" / "observations.jsonl"

    def _stub_evaluate_memory(*_args, **_kwargs) -> EvaluationSummary:
        return EvaluationSummary(
            total_records=0,
            eligible_records=0,
            updated_records=0,
            skipped_records=0,
            unchanged_records=0,
            backup_path=None,
            details=[],
        )

    legacy.evaluate_memory = _stub_evaluate_memory
    legacy.configure_output_dir(ABLATION_DIR)


async def _run(tickers: list[str], mode: str) -> None:
    _sandbox_shared_state()
    import core.orchestrator.legacy as legacy

    await legacy.main(
        mode=mode,
        tickers=tickers,
        output_dir=ABLATION_DIR,
    )


if __name__ == "__main__":
    import sys

    argv = sys.argv[1:]
    run_mode = "compare"
    for arg in list(argv):
        if arg.startswith("--mode="):
            run_mode = arg.split("=", 1)[1]
            argv.remove(arg)

    selected = normalize_idx_tickers(argv if argv else TICKERS)
    ABLATION_DIR.mkdir(parents=True, exist_ok=True)
    asyncio.run(_run(selected, run_mode))
