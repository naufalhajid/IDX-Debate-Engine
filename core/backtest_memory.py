"""JSONL-backed memory for realized trade outcomes."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from core.settings import settings
from utils.logger_config import logger
from utils.ticker import normalize_idx_ticker


DEFAULT_PATH = settings.backtest_memory_path


class TradeOutcome(BaseModel):
    """Realized outcome for a prior debate verdict."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    ticker: str
    verdict_rating: str
    entry_price: float
    exit_price: float | None
    target_price: float
    stop_loss: float
    entry_date: str
    exit_date: str | None
    outcome: Literal["win", "loss", "breakeven", "open", "timeout_flat"]
    pnl_pct: float | None
    hit_target: bool | None
    hit_stop: bool | None
    confidence_at_entry: float | None
    notes: str
    evaluation_method: str | None = None
    evaluation_reason: str | None = None
    evaluation_date: str | None = None
    holding_period_days: int | None = None
    position_size_pct: float | None = None

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: object) -> str:
        """Keep every persisted outcome on the canonical IDX identity."""
        return normalize_idx_ticker(value)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def calculate_pnl_pct(self) -> TradeOutcome:
        """Fill pnl_pct when exit and entry prices are available."""
        if (
            self.pnl_pct is None
            and self.exit_price is not None
            and self.entry_price != 0
        ):
            self.pnl_pct = (self.exit_price - self.entry_price) / self.entry_price * 100
        return self


class BacktestMemory:
    """Append-only JSONL store for trade outcomes."""

    def __init__(self, path: str | Path = DEFAULT_PATH):
        self.path = Path(path)

    def record(self, outcome: TradeOutcome) -> None:
        """Append one trade outcome as a JSON line."""
        validated = TradeOutcome.model_validate(outcome.model_dump())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(validated.model_dump_json())
            handle.write("\n")

    def all_records(self) -> list[TradeOutcome]:
        """Return all stored records in file order."""
        return self._read_all()

    def replace_all(
        self,
        records: Sequence[TradeOutcome],
        *,
        backup: bool = True,
    ) -> Path | None:
        """Atomically replace the memory file, optionally keeping a .bak copy."""
        validated_records = [
            TradeOutcome.model_validate(record.model_dump()) for record in records
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        backup_path: Path | None = None

        if backup and self.path.exists():
            backup_path = self.path.with_name(f"{self.path.name}.bak")
            backup_path.write_text(
                self.path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        tmp_path = self.path.with_name(f"{self.path.name}.tmp")
        payload = "".join(
            record.model_dump_json() + "\n" for record in validated_records
        )
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.path)
        return backup_path

    def query(
        self,
        ticker: str | None = None,
        verdict_rating: str | None = None,
        outcome: str | None = None,
    ) -> list[TradeOutcome]:
        """Read outcomes matching all provided filters."""
        ticker_filter = normalize_idx_ticker(ticker) if ticker is not None else None
        verdict_filter = verdict_rating.upper() if verdict_rating else None
        outcome_filter = outcome.lower() if outcome else None
        return [
            record
            for record in self._read_all()
            if (ticker_filter is None or record.ticker == ticker_filter)
            and (
                verdict_filter is None
                or record.verdict_rating.upper() == verdict_filter
            )
            and (outcome_filter is None or record.outcome == outcome_filter)
        ]

    def summary_stats(self, ticker: str | None = None) -> dict:
        """Return aggregate realized-outcome statistics."""
        records = self.query(ticker=ticker, verdict_rating=None, outcome=None)
        total = len(records)
        wins = sum(1 for record in records if record.outcome == "win")
        losses = sum(1 for record in records if record.outcome == "loss")
        open_count = sum(1 for record in records if record.outcome == "open")
        closed = [
            record
            for record in records
            if record.outcome in {"win", "loss", "breakeven"}
        ]
        pnl_values = [
            record.pnl_pct for record in records if record.pnl_pct is not None
        ]
        confidence_values = [
            record.confidence_at_entry
            for record in records
            if record.confidence_at_entry is not None
        ]
        win_rate = wins / len(closed) if closed else 0.0
        avg_pnl_pct = sum(pnl_values) / len(pnl_values) if pnl_values else None
        avg_confidence = (
            sum(confidence_values) / len(confidence_values)
            if confidence_values
            else None
        )
        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "open": open_count,
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl_pct,
            "avg_confidence": avg_confidence,
        }

    def clear(self) -> None:
        """Remove all stored outcomes. Intended for tests."""
        if self.path.exists():
            self.path.unlink()

    def _read_all(self) -> list[TradeOutcome]:
        if not self.path.exists():
            return []
        records: list[TradeOutcome] = []
        for line_number, line in enumerate(
            self.path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            if not line.strip():
                continue
            try:
                records.append(TradeOutcome.model_validate_json(line))
            except Exception as exc:
                logger.warning(
                    "[BacktestMemory] reason_code=invalid_backtest_memory_record "
                    "line={} exception_type={} detail={}",
                    line_number,
                    type(exc).__name__,
                    exc,
                )
        return records


DEFAULT_MEMORY = BacktestMemory()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Query backtest outcome memory.")
    parser.add_argument("--ticker", help="Filter summary by ticker")
    parser.add_argument(
        "--path",
        default=str(DEFAULT_PATH),
        help="Path to backtest memory JSONL file",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    memory = BacktestMemory(args.path)
    print(json.dumps(memory.summary_stats(args.ticker), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
