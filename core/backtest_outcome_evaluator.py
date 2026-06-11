"""Auto-label open backtest memory records using historical OHLCV data."""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import io
import json
import logging
from pathlib import Path
from typing import Callable, Iterable, Literal

from core.backtest_memory import BacktestMemory, DEFAULT_PATH, TradeOutcome
from core.settings import settings
from utils.logger_config import logger


DEFAULT_HORIZON_TRADING_DAYS = 45
EVALUATED_RATINGS = {"BUY", "STRONG_BUY", "HOLD"}
Outcome = Literal["win", "loss", "timeout_flat"]

# Per-request network timeout (seconds) for yfinance. Without it, a single
# stalled socket blocks the whole startup backtest-eval indefinitely — the
# pipeline appears to hang ("macet") before any analysis begins.
_YF_DOWNLOAD_TIMEOUT_S = 15


@dataclass(frozen=True)
class PriceBar:
    trade_date: date
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class RecordEvaluation:
    ticker: str
    run_id: str
    status: Literal["updated", "skipped", "unchanged"]
    reason: str
    updated_record: TradeOutcome | None = None


@dataclass(frozen=True)
class EvaluationSummary:
    total_records: int
    eligible_records: int
    updated_records: int
    skipped_records: int
    unchanged_records: int
    backup_path: str | None
    details: list[RecordEvaluation]

    def to_dict(self, *, include_details: bool = True) -> dict:
        payload = {
            "total_records": self.total_records,
            "eligible_records": self.eligible_records,
            "updated_records": self.updated_records,
            "skipped_records": self.skipped_records,
            "unchanged_records": self.unchanged_records,
            "backup_path": self.backup_path,
        }
        if include_details:
            payload["details"] = [
                {
                    "ticker": detail.ticker,
                    "run_id": detail.run_id,
                    "status": detail.status,
                    "reason": detail.reason,
                }
                for detail in self.details
            ]
        return payload


PriceFetcher = Callable[[str, date, date], list[PriceBar]]


def _get_yfinance():
    import yfinance as yf

    return yf


def evaluate_trade_outcome(
    record: TradeOutcome,
    bars: Iterable[PriceBar],
    *,
    horizon_trading_days: int = DEFAULT_HORIZON_TRADING_DAYS,
    evaluation_date: date | None = None,
) -> TradeOutcome | None:
    """Return an updated win/loss record, or None when it is too early to score."""
    sorted_bars = sorted(
        (bar for bar in bars if bar.trade_date > _parse_date(record.entry_date)),
        key=lambda bar: bar.trade_date,
    )
    if not sorted_bars:
        return None

    bounded_bars = sorted_bars[:horizon_trading_days]
    for index, bar in enumerate(bounded_bars, start=1):
        stop_hit = bar.low <= record.stop_loss
        target_hit = bar.high >= record.target_price
        if stop_hit:
            return _with_evaluation(
                record,
                outcome="loss",
                exit_price=record.stop_loss,
                exit_date=bar.trade_date,
                hit_target=target_hit,
                hit_stop=True,
                holding_period_days=index,
                evaluation_reason=(
                    "same_day_target_and_stop" if target_hit else "stop_hit"
                ),
                evaluation_date=evaluation_date,
            )
        if target_hit:
            return _with_evaluation(
                record,
                outcome="win",
                exit_price=record.target_price,
                exit_date=bar.trade_date,
                hit_target=True,
                hit_stop=False,
                holding_period_days=index,
                evaluation_reason="target_hit",
                evaluation_date=evaluation_date,
            )

    if len(sorted_bars) < horizon_trading_days:
        return None

    horizon_bar = sorted_bars[horizon_trading_days - 1]
    within_2pct = (
        abs(horizon_bar.close - record.entry_price) / record.entry_price <= 0.02
        if record.entry_price != 0
        else False
    )
    if within_2pct:
        outcome: Outcome = "timeout_flat"
        evaluation_reason = "timeout_flat"
    elif horizon_bar.close > record.entry_price:
        outcome = "win"
        evaluation_reason = "horizon_close_above_entry"
    else:
        outcome = "loss"
        evaluation_reason = "horizon_close_at_or_below_entry"
    return _with_evaluation(
        record,
        outcome=outcome,
        exit_price=horizon_bar.close,
        exit_date=horizon_bar.trade_date,
        hit_target=False,
        hit_stop=False,
        holding_period_days=horizon_trading_days,
        evaluation_reason=evaluation_reason,
        evaluation_date=evaluation_date,
    )


def evaluate_memory(
    *,
    memory_path: Path = DEFAULT_PATH,
    debates_dir: Path = settings.debates_dir,
    write: bool = False,
    horizon_trading_days: int = DEFAULT_HORIZON_TRADING_DAYS,
    price_fetcher: PriceFetcher | None = None,
    today: date | None = None,
) -> EvaluationSummary:
    """Evaluate eligible open BUY/STRONG_BUY records and optionally rewrite memory."""
    memory = BacktestMemory(memory_path)
    records = memory.all_records()
    updated_records: list[TradeOutcome] = []
    details: list[RecordEvaluation] = []
    eligible_count = 0
    changed_count = 0
    fetcher = price_fetcher or fetch_yfinance_price_bars
    evaluation_day = today or date.today()

    # ── Pre-pass: dedup network fetches by ticker ─────────────────────────────
    # Open records frequently share a ticker (e.g. 81 records → 32 tickers).
    # Fetch each ticker's bars ONCE over the widest range any of its records
    # needs. evaluate_trade_outcome re-filters per record by its own entry_date,
    # so a superset of bars yields identical outcomes with far fewer requests —
    # the difference between a ~2-minute and a ~45-second startup.
    earliest_start: dict[str, date] = {}
    for record in records:
        if record.outcome != "open":
            continue
        if record.verdict_rating.upper() not in EVALUATED_RATINGS:
            continue
        if not _matching_debate_artifact_exists(record, debates_dir):
            continue
        try:
            entry_date = _parse_date(record.entry_date)
        except Exception:
            continue
        if entry_date >= evaluation_day:
            continue
        key = record.ticker.upper()
        start = entry_date + timedelta(days=1)
        if key not in earliest_start or start < earliest_start[key]:
            earliest_start[key] = start

    bars_by_ticker: dict[str, list[PriceBar]] = {}
    fetch_failed: set[str] = set()
    for key, start in earliest_start.items():
        try:
            bars_by_ticker[key] = fetcher(key, start, evaluation_day)
        except Exception as exc:
            logger.warning(f"[BacktestEval] {key}: price fetch failed: {exc}")
            fetch_failed.add(key)

    for record in records:
        updated_records.append(record)
        rating = record.verdict_rating.upper()
        if record.outcome != "open":
            details.append(_detail(record, "unchanged", "already_evaluated"))
            continue
        if rating not in EVALUATED_RATINGS:
            details.append(_detail(record, "skipped", "rating_not_evaluated"))
            continue

        eligible_count += 1
        if not _matching_debate_artifact_exists(record, debates_dir):
            details.append(_detail(record, "skipped", "missing_debate_artifact"))
            continue

        try:
            entry_date = _parse_date(record.entry_date)
        except Exception as exc:
            logger.warning(f"[BacktestEval] {record.ticker}: bad entry_date: {exc}")
            details.append(_detail(record, "skipped", "price_fetch_failed"))
            continue
        if entry_date >= evaluation_day:
            details.append(_detail(record, "skipped", "too_early_to_evaluate"))
            continue

        key = record.ticker.upper()
        if key in fetch_failed:
            details.append(_detail(record, "skipped", "price_fetch_failed"))
            continue
        bars = bars_by_ticker.get(key, [])
        if not bars:
            details.append(_detail(record, "skipped", "no_price_data"))
            continue

        evaluated = evaluate_trade_outcome(
            record,
            bars,
            horizon_trading_days=horizon_trading_days,
            evaluation_date=evaluation_day,
        )
        if evaluated is None:
            details.append(_detail(record, "skipped", "insufficient_horizon"))
            continue

        updated_records[-1] = evaluated
        changed_count += 1
        details.append(
            _detail(evaluated, "updated", evaluated.evaluation_reason or "updated")
        )

    backup_path = None
    if write and changed_count:
        backup = memory.replace_all(updated_records, backup=True)
        backup_path = str(backup) if backup else None

    skipped_count = sum(1 for detail in details if detail.status == "skipped")
    unchanged_count = sum(1 for detail in details if detail.status == "unchanged")
    return EvaluationSummary(
        total_records=len(records),
        eligible_records=eligible_count,
        updated_records=changed_count,
        skipped_records=skipped_count,
        unchanged_records=unchanged_count,
        backup_path=backup_path,
        details=details,
    )


def fetch_yfinance_price_bars(ticker: str, start: date, end: date) -> list[PriceBar]:
    """Download daily OHLCV bars from yfinance for an IDX ticker."""
    symbol = ticker.upper()
    if not symbol.endswith(".JK"):
        symbol = f"{symbol}.JK"

    yf_logger = logging.getLogger("yfinance")
    previous_disabled = yf_logger.disabled
    try:
        yf_logger.disabled = True
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            frame = _get_yfinance().download(
                symbol,
                start=start.isoformat(),
                end=(end + timedelta(days=1)).isoformat(),
                progress=False,
                auto_adjust=False,
                threads=False,
                timeout=_YF_DOWNLOAD_TIMEOUT_S,
            )
    finally:
        yf_logger.disabled = previous_disabled
    if frame is None or frame.empty:
        return []

    if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
        frame.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in frame.columns.to_flat_index()
        ]

    bars: list[PriceBar] = []
    for index, row in frame.iterrows():
        try:
            trade_date = (
                index.date() if hasattr(index, "date") else _parse_date(str(index))
            )
            bars.append(
                PriceBar(
                    trade_date=trade_date,
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                )
            )
        except Exception as exc:
            logger.debug(f"[BacktestEval] skipped malformed yfinance row: {exc}")
    return bars


def _with_evaluation(
    record: TradeOutcome,
    *,
    outcome: Outcome,
    exit_price: float,
    exit_date: date,
    hit_target: bool,
    hit_stop: bool,
    holding_period_days: int,
    evaluation_reason: str,
    evaluation_date: date | None,
) -> TradeOutcome:
    payload = record.model_dump()
    payload.update(
        {
            "exit_price": float(exit_price),
            "exit_date": exit_date.isoformat(),
            "outcome": outcome,
            "pnl_pct": None,
            "hit_target": hit_target,
            "hit_stop": hit_stop,
            "evaluation_method": "hybrid_target_stop_horizon",
            "evaluation_reason": evaluation_reason,
            "evaluation_date": (evaluation_date or date.today()).isoformat(),
            "holding_period_days": holding_period_days,
        }
    )
    return TradeOutcome(**payload)


def _matching_debate_artifact_exists(record: TradeOutcome, debates_dir: Path) -> bool:
    ticker = record.ticker.upper()
    return (
        debates_dir / ticker / f"v{record.run_id}" / f"{ticker}_debate.json"
    ).exists()


def _detail(
    record: TradeOutcome,
    status: Literal["updated", "skipped", "unchanged"],
    reason: str,
) -> RecordEvaluation:
    return RecordEvaluation(
        ticker=record.ticker,
        run_id=record.run_id,
        status=status,
        reason=reason,
        updated_record=record if status == "updated" else None,
    )


def _parse_date(value: str) -> date:
    return datetime.fromisoformat(value[:10]).date()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate open BUY/STRONG_BUY backtest memory records."
    )
    parser.add_argument(
        "--memory-path",
        default=str(DEFAULT_PATH),
        help="Path to backtest memory JSONL file.",
    )
    parser.add_argument(
        "--debates-dir",
        default="output/debates",
        help="Path to versioned debate artifacts.",
    )
    parser.add_argument(
        "--horizon-days",
        type=int,
        default=DEFAULT_HORIZON_TRADING_DAYS,
        help="Trading-day horizon for hybrid evaluation.",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Rewrite memory with evaluated outcomes. Without this, only prints a report.",
    )
    parser.add_argument(
        "--no-details",
        action="store_true",
        help="Omit per-record detail rows from stdout.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    summary = evaluate_memory(
        memory_path=Path(args.memory_path),
        debates_dir=Path(args.debates_dir),
        write=args.write,
        horizon_trading_days=args.horizon_days,
    )
    print(json.dumps(summary.to_dict(include_details=not args.no_details), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
