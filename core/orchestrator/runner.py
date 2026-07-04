"""Narrow production runner interface for the orchestrator pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import legacy


@dataclass(slots=True)
class PipelineRunConfig:
    """Configuration boundary for a production pipeline run."""

    dry_run: bool = False
    output_dir: Path | None = None
    portfolio_state: dict[str, Any] | None = None
    user_config: dict[str, Any] | None = None
    mode: str | None = None
    screener_mode: str | None = None
    chamber_factory: Callable[[], Any] | None = None
    tickers: list[str] | None = None
    research_compare: bool = False
    raise_on_error: bool = False


class PipelineRunner:
    """Public orchestration facade while legacy internals are strangled."""

    def __init__(self, config: PipelineRunConfig | None = None) -> None:
        self.config = config or PipelineRunConfig()

    async def run(self) -> None:
        output_dir = (
            Path(self.config.output_dir)
            if self.config.output_dir is not None
            else legacy.OUTPUT_DIR
        )
        legacy.configure_output_dir(output_dir)
        await legacy.main(
            dry_run=self.config.dry_run,
            output_dir=output_dir,
            portfolio_state=self.config.portfolio_state,
            user_config=self.config.user_config,
            mode=self.config.mode,
            screener_mode=self.config.screener_mode,
            chamber_factory=self.config.chamber_factory,
            tickers=self.config.tickers,
            research_compare=self.config.research_compare,
            raise_on_error=self.config.raise_on_error,
        )


async def run_pipeline(config: PipelineRunConfig | None = None) -> None:
    await PipelineRunner(config).run()
