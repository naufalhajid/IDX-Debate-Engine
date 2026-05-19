from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from core.orchestrator import legacy as _legacy

# Backward-compatible facade: expose all legacy symbols, including private
# helpers that tests or local scripts may import directly from orchestrator.py.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


def __getattr__(name: str) -> Any:
    return getattr(_legacy, name)


def _sync_path_globals() -> None:
    for _name in ("OUTPUT_DIR", "JSON_PATH", "FULL_RESULTS_PATH", "TOP3_REPORT_PATH"):
        globals()[_name] = getattr(_legacy, _name)


def configure_output_dir(output_dir: Path) -> None:
    _legacy.configure_output_dir(output_dir)
    _sync_path_globals()


def _run_cli(argv: list[str] | None = None) -> None:
    _legacy._ensure_utf8_stdout()
    args = _legacy._parse_cli_args(argv)
    _legacy.configure_cli_logging(verbose=args.verbose)
    configure_output_dir(Path(args.output_dir))
    scrape_cmd = _legacy.shlex.split(args.scrape_cmd) if args.scrape_cmd else None
    _legacy._cli.run(
        interactive=not args.no_interactive,
        skip_scraping=args.skip_scraping,
        scrape_cmd=scrape_cmd,
    )
    user_config = (
        {"total_capital": 1_000_000.0, "max_loss_pct": 0.02, "max_positions": 5}
        if args.no_interactive
        else None
    )
    asyncio.run(
        _legacy.main(
            dry_run=args.dry_run,
            output_dir=_legacy.OUTPUT_DIR,
            user_config=user_config,
        )
    )


if __name__ == "__main__":
    _run_cli()
