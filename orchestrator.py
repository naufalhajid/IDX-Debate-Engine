from __future__ import annotations

# ruff: noqa: E402

from dotenv import load_dotenv

load_dotenv()

import asyncio
from pathlib import Path
from typing import Any

from core.orchestrator import pipeline as _pipeline

# Backward-compatible facade: expose all pipeline symbols, including private
# helpers that tests or local scripts may import directly from orchestrator.py.
for _name in dir(_pipeline):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_pipeline, _name)


def __getattr__(name: str) -> Any:
    return getattr(_pipeline, name)


def _sync_path_globals() -> None:
    for _name in ("OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"):
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
    asyncio.run(
        _pipeline.main(
            dry_run=args.dry_run,
            output_dir=_pipeline.OUTPUT_DIR,
            user_config=user_config,
            raise_on_error=True,
        )
    )


if __name__ == "__main__":
    _run_cli()
