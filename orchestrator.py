from __future__ import annotations

import warnings

_original_showwarning = warnings.showwarning


def _custom_showwarning(message, category, filename, lineno, file=None, line=None):
    if "allowed_objects" in str(message) or "LangChain" in category.__name__:
        return
    _original_showwarning(message, category, filename, lineno, file, line)


warnings.showwarning = _custom_showwarning

# ruff: noqa: E402

from dotenv import load_dotenv

load_dotenv()

import asyncio
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from core.orchestrator import pipeline as _pipeline
from core.settings import settings

# Backward-compatible facade: expose all pipeline symbols, including private
# helpers that tests or local scripts may import directly from orchestrator.py.
for _name in dir(_pipeline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_pipeline, _name)


def __getattr__(name: str) -> Any:
    return getattr(_pipeline, name)


def _sync_path_globals() -> None:
    for _name in (
        "OUTPUT_DIR",
        "JSON_PATH",
        "FULL_RESULTS_PATH",
        "MERGED_RESULTS_PATH",
        "TOP3_REPORT_PATH",
    ):
        globals()[_name] = getattr(_pipeline, _name)


def configure_output_dir(output_dir: Path) -> None:
    _pipeline.configure_output_dir(output_dir)
    _sync_path_globals()


def _run_cli(argv: list[str] | None = None) -> None:
    _pipeline._ensure_utf8_stdout()
    args = _pipeline._parse_cli_args(argv)
    _pipeline.configure_cli_logging(verbose=args.verbose)
    _pipeline._cli_renderer.show_details = getattr(args, "details", False)
    configure_output_dir(Path(args.output_dir))
    scrape_cmd = _pipeline.shlex.split(args.scrape_cmd) if args.scrape_cmd else None
    pipeline_ok = _pipeline._cli.run(
        interactive=not args.no_interactive,
        skip_scraping=args.skip_scraping,
        scrape_cmd=scrape_cmd,
    )
    if not pipeline_ok:
        raise SystemExit(1)
    user_config = (
        {"total_capital": 1_000_000.0, "max_loss_pct": 0.02, "max_positions": 5}
        if args.no_interactive
        else None
    )
    cli_portfolio_state: dict | None = None
    _loss_pct = getattr(args, "portfolio_loss_pct", None)
    if _loss_pct is not None:
        cli_portfolio_state = {
            "realized_loss_pct": -abs(float(_loss_pct) / 100.0)
        }
    # Codex-only: batch runs (no explicit tickers, or more than the deep-mode
    # cap) drop reasoning effort for speed; other providers are unaffected.
    reasoning_context = nullcontext()
    if str(settings.DEFAULT_LLM_PROVIDER or "").lower() == "codex":
        from providers.codex_adapter import (
            DEEP_REASONING_MAX_TICKERS,
            codex_reasoning_override,
        )

        explicit_tickers = list(args.tickers or [])
        if not (0 < len(explicit_tickers) <= DEEP_REASONING_MAX_TICKERS):
            reasoning_context = codex_reasoning_override(flash=None, pro=None)

    with reasoning_context:
        asyncio.run(
            _pipeline.main(
                dry_run=args.dry_run,
                output_dir=_pipeline.OUTPUT_DIR,
                portfolio_state=cli_portfolio_state,
                user_config=user_config,
                raise_on_error=True,
            )
        )


if __name__ == "__main__":
    _run_cli()
