"""Load historical CIOVerdict signals from versioned debate JSON files."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from core.backtest_memory import TradeOutcome


_FOLDER_RE = re.compile(r"^v(\d{8})_(\d{6})$")


@dataclass(frozen=True)
class SignalRecord:
    run_id: str
    ticker: str
    signal_date: date
    rating: str
    confidence: float
    entry_price: float
    target_price: float
    stop_loss: float
    current_price: float | None
    regime: str | None = None


def scan_debate_dir(
    debates_dir: Path,
    *,
    min_rating: str = "BUY",
    from_date: date | None = None,
    tickers: list[str] | None = None,
) -> list[SignalRecord]:
    """Scan versioned debate JSONs and return valid BUY/STRONG_BUY signals.

    De-duplicates same (ticker, signal_date) by keeping the latest folder version.
    Skips signals with missing target_price or stop_loss.
    """
    allowed = _allowed_ratings(min_rating)
    ticker_filter = {t.upper() for t in tickers} if tickers else None

    # Collect best (ticker, signal_date) → (folder_name, json_path)
    best: dict[tuple[str, date], tuple[str, Path]] = {}

    for ticker_dir in sorted(debates_dir.iterdir()):
        if not ticker_dir.is_dir():
            continue
        ticker = ticker_dir.name.upper()
        if ticker_filter and ticker not in ticker_filter:
            continue

        # sorted(reverse=True) ensures latest folder is first
        for version_dir in sorted(ticker_dir.iterdir(), reverse=True):
            if not version_dir.is_dir():
                continue
            if not _FOLDER_RE.match(version_dir.name):
                continue
            json_path = version_dir / f"{ticker_dir.name}_debate.json"
            if not json_path.exists():
                continue
            try:
                signal_date = _parse_signal_date(version_dir.name)
            except ValueError:
                continue

            key = (ticker, signal_date)
            if key not in best:
                best[key] = (version_dir.name, json_path)

    records: list[SignalRecord] = []
    for (ticker, signal_date), (folder_name, json_path) in best.items():
        if from_date and signal_date < from_date:
            continue
        try:
            signal = _load_signal(ticker, signal_date, folder_name, json_path, allowed)
        except Exception:
            continue
        if signal is not None:
            records.append(signal)

    return sorted(records, key=lambda r: (r.signal_date, r.ticker))


def signals_to_outcomes(
    signals: list[SignalRecord],
    existing_run_ids: set[tuple[str, str]],
) -> list[TradeOutcome]:
    """Convert SignalRecords to open TradeOutcome records, skipping already-recorded ones."""
    outcomes: list[TradeOutcome] = []
    for sig in signals:
        key = (sig.ticker.upper(), sig.run_id)
        if key in existing_run_ids:
            continue
        regime_tag = f"regime={sig.regime}" if sig.regime else "regime=UNKNOWN"
        outcomes.append(
            TradeOutcome(
                run_id=sig.run_id,
                ticker=sig.ticker,
                verdict_rating=sig.rating,
                entry_price=sig.entry_price,
                exit_price=None,
                target_price=sig.target_price,
                stop_loss=sig.stop_loss,
                entry_date=sig.signal_date.isoformat(),
                exit_date=None,
                outcome="open",
                pnl_pct=None,
                hit_target=None,
                hit_stop=None,
                confidence_at_entry=sig.confidence,
                notes=f"{regime_tag};loaded_by=backtester_v1",
            )
        )
    return outcomes


def build_existing_run_ids(records: list[TradeOutcome]) -> set[tuple[str, str]]:
    """Build idempotency set from existing memory records."""
    return {(r.ticker.upper(), r.run_id) for r in records}


def _load_signal(
    ticker: str,
    signal_date: date,
    folder_name: str,
    json_path: Path,
    allowed: frozenset[str],
) -> SignalRecord | None:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    verdict = data.get("verdict", {})

    rating = (verdict.get("rating") or "").upper()
    if rating not in allowed:
        return None

    target_price = verdict.get("target_price")
    stop_loss = verdict.get("stop_loss")
    if target_price is None or stop_loss is None:
        return None

    confidence = float(verdict.get("confidence") or 0.0)
    current_price = verdict.get("current_price")
    entry_price_range = verdict.get("entry_price_range")
    entry_price = _parse_entry_high(entry_price_range, current_price)
    if entry_price is None:
        return None

    regime = (data.get("metadata") or {}).get("regime") or None
    run_id = folder_name.lstrip("v")
    return SignalRecord(
        run_id=run_id,
        ticker=ticker,
        signal_date=signal_date,
        rating=rating,
        confidence=confidence,
        entry_price=entry_price,
        target_price=float(target_price),
        stop_loss=float(stop_loss),
        current_price=float(current_price) if current_price is not None else None,
        regime=regime,
    )


def _parse_signal_date(folder_name: str) -> date:
    """Extract date from folder name 'v20260612_211545' → date(2026, 6, 12)."""
    match = _FOLDER_RE.match(folder_name)
    if not match:
        raise ValueError(f"Folder '{folder_name}' does not match vYYYYMMDD_HHMMSS")
    date_str = match.group(1)
    return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))


def _parse_entry_high(
    entry_price_range: str | None,
    current_price: object | None,
) -> float | None:
    """Parse upper bound from 'XXXX - YYYY', handling Indonesian dot-separators.

    Falls back to current_price if range is missing or unparseable.
    """
    if entry_price_range:
        parts = re.split(r"\s*[-–—]\s*", entry_price_range.strip(), maxsplit=1)
        if len(parts) == 2:
            raw = parts[1].strip()
            # Remove Indonesian thousands separator (dot before groups of 3 digits)
            raw_clean = re.sub(r"\.(?=\d{3}(?:[.,]|$))", "", raw)
            raw_clean = raw_clean.replace(",", ".")
            try:
                return float(raw_clean)
            except ValueError:
                pass

    if current_price is not None:
        try:
            return float(current_price)
        except (ValueError, TypeError):
            pass

    return None


def _allowed_ratings(min_rating: str) -> frozenset[str]:
    if min_rating.upper() == "STRONG_BUY":
        return frozenset({"STRONG_BUY"})
    return frozenset({"BUY", "STRONG_BUY"})
