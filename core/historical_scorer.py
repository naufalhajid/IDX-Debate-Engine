"""
core/historical_scorer.py — Rule-based historical pattern scorer.

Membaca debate result JSON dari output/debates/*.json untuk menghitung
win rate historis per ticker sebagai adjustment atas conviction score.

Threshold rationale:
  - _WIN_RATE_HIGH_THRESHOLD (0.70): Ticker yang konsisten menghasilkan sinyal BUY/STRONG_BUY
    yang valid (confidence > 50%) di >= 70% run historis mendapat bonus. Angka 70% dipilih
    karena di bawahnya tidak cukup konsisten untuk dijadikan edge statistik.
  - _WIN_RATE_LOW_THRESHOLD (0.30): Ticker yang hanya menghasilkan sinyal valid di < 30%
    run historis menunjukkan noise-to-signal ratio tinggi — dapat penalty.
  - _BONUS / _PENALTY (+/-0.05): Dikalibrasi untuk menggeser ranking ~1 posisi dalam
    distribusi conviction score tipikal (0.30–0.80 range). Cukup signifikan untuk
    mempengaruhi rank tapi tidak mendominasi signal dari fundamental LLM.
  - _MIN_RECORDS_FOR_ADJUSTMENT (3): Guard terhadap noise dari sample kecil — kurang dari
    3 run historis tidak cukup untuk membuat keputusan statistik yang bermakna.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from core.backtest_memory import DEFAULT_PATH, BacktestMemory, TradeOutcome
from utils.logger_config import logger
from utils.ticker import (
    InvalidIDXTicker,
    PathContainmentError,
    normalize_idx_ticker,
    resolve_within_root,
)

# \u2500\u2500 Constants \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500

_MIN_RECORDS_FOR_ADJUSTMENT: int = 10
_HALF_ADJUSTMENT_THRESHOLD: int = 15  # below this: half bonus/penalty applied
_WIN_RATE_HIGH_THRESHOLD: float = 0.70
_WIN_RATE_LOW_THRESHOLD: float = 0.30
_REALIZED_WIN_RATE_HIGH_THRESHOLD: float = 0.60
_REALIZED_WIN_RATE_LOW_THRESHOLD: float = 0.40
_EV_HIGH_THRESHOLD: float = 3.0  # avg pnl% above this → bonus
_EV_LOW_THRESHOLD: float = -2.0  # avg pnl% below this → penalty
_BONUS: float = 0.05
_PENALTY: float = -0.05
_MAX_HISTORY_RECORDS_PER_TICKER: int = 20

_NESTED_TICKER_FIELDS = ("verdict", "risk_governor", "execution_decision")


def _canonicalize_history_record(
    data: Any,
    *,
    expected_ticker: str | None = None,
) -> dict[str, Any]:
    """Validate one debate record's complete ticker identity."""
    if not isinstance(data, dict):
        raise ValueError("debate history record must be a JSON object")
    ticker = normalize_idx_ticker(data.get("ticker"))
    if expected_ticker is not None and ticker != expected_ticker:
        raise ValueError(
            f"payload ticker {ticker} does not match artifact ticker {expected_ticker}"
        )

    normalized = dict(data)
    normalized["ticker"] = ticker
    for field in _NESTED_TICKER_FIELDS:
        nested = data.get(field)
        if not isinstance(nested, dict):
            continue
        nested_ticker = nested.get("ticker")
        if nested_ticker not in (None, ""):
            canonical_nested = normalize_idx_ticker(nested_ticker)
            if canonical_nested != ticker:
                raise ValueError(
                    f"{field} ticker {canonical_nested} does not match {ticker}"
                )
        normalized[field] = {**nested, "ticker": ticker}
    return normalized


def _validated_history_records(records: Sequence[Any]) -> list[dict[str, Any]]:
    """Skip corrupt caller-provided records without aborting the score."""
    validated: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        try:
            validated.append(_canonicalize_history_record(record))
        except Exception as exc:
            logger.warning(
                "[HistScorer] reason_code=invalid_history_record record={} "
                "exception_type={} detail={}",
                index,
                type(exc).__name__,
                exc,
            )
    return validated


# \u2500\u2500 Public API \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500


def load_debate_history(output_dir: Path) -> list[dict]:
    """
    Muat semua debate result JSON dari output/debates/.

    Format yang diharapkan: {ticker, verdict: {rating, confidence}, ...}
    File yang rusak/tidak bisa di-parse diabaikan dengan silent warning.
    """
    try:
        debates_dir = resolve_within_root(output_dir, "debates")
    except PathContainmentError as exc:
        logger.error(f"[HistScorer] Unsafe debates directory: {exc}")
        return []
    records: list[dict] = []

    if not debates_dir.exists():
        logger.debug(
            f"[HistScorer] Direktori {debates_dir} tidak ada — tidak ada history."
        )
        return records

    versioned_by_ticker: dict[str, list[Path]] = {}
    flat_files: list[tuple[str, Path]] = []
    for candidate in debates_dir.iterdir():
        if candidate.name.endswith("_debate.json"):
            raw_ticker = candidate.name.removesuffix("_debate.json")
            try:
                ticker = normalize_idx_ticker(raw_ticker)
                if ticker != raw_ticker:
                    continue
                flat_path = resolve_within_root(
                    debates_dir,
                    f"{ticker}_debate.json",
                )
            except (InvalidIDXTicker, PathContainmentError):
                continue
            if flat_path.is_file():
                flat_files.append((ticker, flat_path))
            continue

        try:
            ticker = normalize_idx_ticker(candidate.name)
            if ticker != candidate.name:
                continue
            ticker_dir = resolve_within_root(debates_dir, ticker)
        except (InvalidIDXTicker, PathContainmentError):
            continue
        if not ticker_dir.is_dir():
            continue
        for candidate_version in ticker_dir.iterdir():
            version_name = candidate_version.name
            if not version_name.startswith("v"):
                continue
            try:
                version_dir = resolve_within_root(
                    debates_dir,
                    ticker,
                    version_name,
                )
                debate_path = resolve_within_root(
                    debates_dir,
                    ticker,
                    version_name,
                    f"{ticker}_debate.json",
                )
            except PathContainmentError:
                continue
            if version_dir.is_dir() and debate_path.is_file():
                versioned_by_ticker.setdefault(ticker, []).append(debate_path)

    files: list[tuple[str, Path]] = []
    for ticker, ticker_files in versioned_by_ticker.items():
        files.extend(
            (ticker, path)
            for path in sorted(
                ticker_files,
                key=lambda p: p.parent.name,
                reverse=True,
            )[:_MAX_HISTORY_RECORDS_PER_TICKER]
        )

    versioned_tickers = set(versioned_by_ticker)
    files.extend(
        (ticker, path)
        for ticker, path in flat_files
        if ticker not in versioned_tickers
    )

    for expected_ticker, f in files:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            records.append(
                _canonicalize_history_record(
                    data,
                    expected_ticker=expected_ticker,
                )
            )
        except Exception as exc:
            logger.warning(
                "[HistScorer] reason_code=invalid_debate_history_record file={} "
                "exception_type={} detail={}",
                f.name,
                type(exc).__name__,
                exc,
            )

    logger.info(f"[HistScorer] Loaded {len(records)} debate records dari {debates_dir}")
    return records


def compute_historical_win_rate(ticker: str, records: list[dict]) -> float | None:
    """
    Hitung win rate historis untuk satu ticker.

    Win = debate dengan rating BUY/STRONG_BUY dan confidence > 0.50.
    Conviction score LLM bisa menghasilkan BUY tapi dengan confidence rendah —
    threshold 50% memastikan hanya sinyal yang cukup kuat yang dihitung sebagai "win".

    Returns:
        float in [0.0, 1.0], atau None jika records < _MIN_RECORDS_FOR_ADJUSTMENT.
    """
    canonical_ticker = normalize_idx_ticker(ticker)
    ticker_records = [
        record
        for record in _validated_history_records(records)
        if record["ticker"] == canonical_ticker
    ]

    if len(ticker_records) < _MIN_RECORDS_FOR_ADJUSTMENT:
        logger.debug(
            f"[HistScorer] {ticker}: {len(ticker_records)} records "
            f"(min={_MIN_RECORDS_FOR_ADJUSTMENT}) — no adjustment."
        )
        return None

    wins = sum(
        1
        for r in ticker_records
        if (
            r.get("verdict")
            if isinstance(r.get("verdict"), dict)
            else {}
        ).get("rating")
        in ("BUY", "STRONG_BUY")
        and float(
            (
                r.get("verdict")
                if isinstance(r.get("verdict"), dict)
                else {}
            ).get("confidence", 0)
            or 0
        )
        > 0.50
    )
    win_rate = wins / len(ticker_records)
    logger.debug(
        f"[HistScorer] {ticker}: {wins}/{len(ticker_records)} wins → "
        f"win_rate={win_rate:.0%}"
    )
    return win_rate


def load_realized_outcomes(memory_path: Path = DEFAULT_PATH) -> list[TradeOutcome]:
    """Load realized trade outcomes from JSONL memory without raising."""
    try:
        return BacktestMemory(memory_path).all_records()
    except Exception as exc:
        logger.warning(f"[HistScorer] Gagal baca realized outcomes: {exc}")
        return []


def compute_realized_win_rate(
    ticker: str,
    records: Sequence[TradeOutcome],
) -> float | None:
    """Return realized win rate for evaluated BUY/STRONG_BUY outcomes."""
    canonical_ticker = normalize_idx_ticker(ticker)
    ticker_records = [
        record
        for record in records
        if normalize_idx_ticker(record.ticker) == canonical_ticker
        and record.verdict_rating.upper() in {"BUY", "STRONG_BUY"}
        and record.outcome in {"win", "loss"}
    ]

    if len(ticker_records) < _MIN_RECORDS_FOR_ADJUSTMENT:
        logger.debug(
            f"[HistScorer] {ticker}: {len(ticker_records)} realized outcomes "
            f"(min={_MIN_RECORDS_FOR_ADJUSTMENT}) - fallback to debate history."
        )
        return None

    wins = sum(1 for record in ticker_records if record.outcome == "win")
    win_rate = wins / len(ticker_records)
    logger.debug(
        f"[HistScorer] {ticker}: realized {wins}/{len(ticker_records)} wins -> "
        f"win_rate={win_rate:.0%}"
    )
    return win_rate


def _scaled_adjustment(base: float, n: int | None) -> float:
    """Half the adjustment when n < _HALF_ADJUSTMENT_THRESHOLD."""
    if n is None or n >= _HALF_ADJUSTMENT_THRESHOLD:
        return base
    return base / 2


def apply_realized_adjustment(
    conviction_score: float,
    win_rate: float | None,
    n: int | None = None,
) -> float:
    """Adjust conviction score with realized outcome thresholds."""
    if win_rate is None:
        return conviction_score

    if win_rate >= _REALIZED_WIN_RATE_HIGH_THRESHOLD:
        bonus = _scaled_adjustment(_BONUS, n)
        adjusted = conviction_score + bonus
        logger.debug(
            f"[HistScorer] Realized win rate {win_rate:.0%} >= "
            f"{_REALIZED_WIN_RATE_HIGH_THRESHOLD:.0%} -> +{bonus} bonus (n={n})"
        )
    elif win_rate <= _REALIZED_WIN_RATE_LOW_THRESHOLD:
        penalty = _scaled_adjustment(_PENALTY, n)
        adjusted = conviction_score + penalty
        logger.debug(
            f"[HistScorer] Realized win rate {win_rate:.0%} <= "
            f"{_REALIZED_WIN_RATE_LOW_THRESHOLD:.0%} -> {penalty} penalty (n={n})"
        )
    else:
        return conviction_score

    return max(0.0, min(adjusted, 1.0))


def apply_historical_adjustment(
    conviction_score: float,
    win_rate: float | None,
    n: int | None = None,
) -> float:
    """Adjust conviction score berdasarkan historical win rate."""
    if win_rate is None:
        return conviction_score

    if win_rate >= _WIN_RATE_HIGH_THRESHOLD:
        bonus = _scaled_adjustment(_BONUS, n)
        adjusted = conviction_score + bonus
        logger.debug(
            f"[HistScorer] Win rate {win_rate:.0%} >= {_WIN_RATE_HIGH_THRESHOLD:.0%} "
            f"-> +{bonus} bonus (n={n})"
        )
    elif win_rate < _WIN_RATE_LOW_THRESHOLD:
        penalty = _scaled_adjustment(_PENALTY, n)
        adjusted = conviction_score + penalty
        logger.debug(
            f"[HistScorer] Win rate {win_rate:.0%} < {_WIN_RATE_LOW_THRESHOLD:.0%} "
            f"-> {penalty} penalty (n={n})"
        )
    else:
        return conviction_score

    return max(0.0, min(adjusted, 1.0))


def compute_realized_ev(
    ticker: str,
    records: Sequence[TradeOutcome],
) -> float | None:
    """Average pnl_pct across closed BUY/STRONG_BUY trades for a ticker.

    Returns None if fewer than _MIN_RECORDS_FOR_ADJUSTMENT closed trades with pnl_pct.
    EV > 0 means the system's signals for this ticker are profitable on average.
    """
    canonical_ticker = normalize_idx_ticker(ticker)
    closed = [
        r
        for r in records
        if normalize_idx_ticker(r.ticker) == canonical_ticker
        and r.verdict_rating.upper() in {"BUY", "STRONG_BUY"}
        and r.outcome in {"win", "loss", "breakeven"}
        and r.pnl_pct is not None
    ]
    if len(closed) < _MIN_RECORDS_FOR_ADJUSTMENT:
        return None
    ev = sum(r.pnl_pct for r in closed) / len(closed)  # type: ignore[arg-type]
    logger.debug(
        f"[HistScorer] {ticker}: realized EV = {ev:.2f}% from {len(closed)} closed trades"
    )
    return ev


def apply_ev_adjustment(
    conviction_score: float,
    ev_pct: float,
    n: int | None = None,
) -> float:
    """Adjust conviction score based on expected value per trade signal.

    EV >= _EV_HIGH_THRESHOLD (3%): system signals profitable → bonus.
    EV < _EV_LOW_THRESHOLD (-2%): system signals losing money → penalty.
    """
    if ev_pct >= _EV_HIGH_THRESHOLD:
        bonus = _scaled_adjustment(_BONUS, n)
        adjusted = conviction_score + bonus
        logger.debug(
            f"[HistScorer] EV {ev_pct:.2f}% >= {_EV_HIGH_THRESHOLD}% -> +{bonus} bonus (n={n})"
        )
    elif ev_pct < _EV_LOW_THRESHOLD:
        penalty = _scaled_adjustment(_PENALTY, n)
        adjusted = conviction_score + penalty
        logger.debug(
            f"[HistScorer] EV {ev_pct:.2f}% < {_EV_LOW_THRESHOLD}% -> {penalty} penalty (n={n})"
        )
    else:
        return conviction_score

    return max(0.0, min(adjusted, 1.0))
