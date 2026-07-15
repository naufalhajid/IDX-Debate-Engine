"""Canonical, reproducible daily OHLCV snapshots for live IDX analysis."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from utils.ticker import normalize_idx_ticker, resolve_within_root, to_yfinance_symbol


SNAPSHOT_CONTRACT_VERSION = "market-snapshot-v1"
DEFAULT_LOOKBACK_CALENDAR_DAYS = 630
DEFAULT_MIN_COMPLETE_BARS = 400
SNAPSHOT_INTERVAL = "1d"
SNAPSHOT_AUTO_ADJUST = True
IDX_TIMEZONE = ZoneInfo("Asia/Jakarta")
_SESSION_FINALIZATION_TIME = time(16, 15)
_OHLCV_COLUMNS = ("Open", "High", "Low", "Close", "Volume")


def _normalise_ticker(ticker: str) -> str:
    return normalize_idx_ticker(ticker)


def snapshot_window(
    as_of: date | None = None,
    *,
    lookback_calendar_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
) -> tuple[date, date]:
    """Return inclusive start/end dates for one reproducible run."""
    end = as_of or datetime.now(IDX_TIMEZONE).date()
    return end - timedelta(days=int(lookback_calendar_days)), end


def _canonical_index(index: Any) -> pd.DatetimeIndex:
    converted = pd.to_datetime(index, errors="coerce")
    if not isinstance(converted, pd.DatetimeIndex):
        converted = pd.DatetimeIndex(converted)
    if converted.tz is not None:
        converted = converted.tz_convert(IDX_TIMEZONE).tz_localize(None)
    return converted.normalize()


def extract_ticker_history(raw: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Extract one ticker from flat, ticker-first, or price-first yf output."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame()
    symbol = to_yfinance_symbol(ticker)
    if not isinstance(raw.columns, pd.MultiIndex):
        return raw.copy()

    level_zero = {
        str(value).upper(): value for value in raw.columns.get_level_values(0)
    }
    if symbol in level_zero:
        return raw.xs(level_zero[symbol], axis=1, level=0, drop_level=True).copy()
    level_one = {
        str(value).upper(): value for value in raw.columns.get_level_values(1)
    }
    if symbol in level_one:
        return raw.xs(level_one[symbol], axis=1, level=1, drop_level=True).copy()
    return pd.DataFrame()


def clean_ohlcv_history(
    raw: pd.DataFrame,
    *,
    ticker: str,
    as_of: date,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, dict[str, int | bool]]:
    """Canonicalize bars and remove duplicate or incomplete session rows."""
    frame = extract_ticker_history(raw, ticker)
    empty_stats: dict[str, int | bool] = {
        "dropped_duplicate_dates": 0,
        "dropped_incomplete_rows": 0,
        "dropped_current_session": False,
    }
    if frame.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS), empty_stats

    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    lookup = {str(column).strip().lower(): column for column in frame.columns}
    missing = [column for column in _OHLCV_COLUMNS if column.lower() not in lookup]
    if missing:
        empty_stats["dropped_incomplete_rows"] = len(frame)
        return pd.DataFrame(columns=_OHLCV_COLUMNS), empty_stats

    frame = frame[[lookup[column.lower()] for column in _OHLCV_COLUMNS]].copy()
    frame.columns = list(_OHLCV_COLUMNS)
    frame.index = _canonical_index(frame.index)
    frame = frame[~frame.index.isna()].sort_index(kind="stable")
    duplicate_count = int(frame.index.duplicated(keep="last").sum())
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame[frame.index.date <= as_of]

    for column in _OHLCV_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    ohlcv = frame.loc[:, list(_OHLCV_COLUMNS)]
    finite = ohlcv.map(
        lambda value: math.isfinite(float(value)) if pd.notna(value) else False
    ).all(axis=1)
    valid_rows = (
        ohlcv.notna().all(axis=1)
        & finite
        & (frame.loc[:, ["Open", "High", "Low", "Close"]] > 0).all(axis=1)
        & (frame["Volume"] >= 0)
    )
    incomplete_count = int((~valid_rows).sum())
    frame = frame.loc[valid_rows].astype("float64")

    resolved_now = now or datetime.now(IDX_TIMEZONE)
    if resolved_now.tzinfo is None:
        resolved_now = resolved_now.replace(tzinfo=IDX_TIMEZONE)
    else:
        resolved_now = resolved_now.astimezone(IDX_TIMEZONE)
    drop_current = (
        as_of == resolved_now.date()
        and resolved_now.time().replace(tzinfo=None) < _SESSION_FINALIZATION_TIME
        and not frame.empty
        and frame.index[-1].date() == as_of
    )
    if drop_current:
        frame = frame.iloc[:-1]
    frame.index.name = "Date"
    return frame, {
        "dropped_duplicate_dates": duplicate_count,
        "dropped_incomplete_rows": incomplete_count,
        "dropped_current_session": bool(drop_current),
    }


def history_data_hash(history: pd.DataFrame) -> str:
    """Hash canonical OHLCV bytes; stable across provider column layouts."""
    payload = history.loc[:, list(_OHLCV_COLUMNS)].to_csv(
        index=True,
        date_format="%Y-%m-%d",
        float_format="%.12g",
        lineterminator="\n",
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MarketSnapshot:
    """One ticker's cleaned daily OHLCV and auditable provenance."""

    ticker: str
    history: pd.DataFrame
    requested_start: date
    requested_end: date
    data_hash: str
    snapshot_id: str
    first_date: date | None
    last_date: date | None
    row_count: int
    status: str
    reason_codes: tuple[str, ...]
    cleaning: Mapping[str, int | bool]
    source: str = "yfinance"
    interval: str = SNAPSHOT_INTERVAL
    auto_adjust: bool = SNAPSHOT_AUTO_ADJUST
    contract_version: str = SNAPSHOT_CONTRACT_VERSION

    @property
    def is_ready(self) -> bool:
        return self.status == "ready"

    def history_copy(self) -> pd.DataFrame:
        return self.history.copy(deep=True)

    def provenance(self, *, artifact_path: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "snapshot_id": self.snapshot_id,
            "data_hash": self.data_hash,
            "ticker": self.ticker,
            "source": self.source,
            "contract_version": self.contract_version,
            "requested_start": self.requested_start.isoformat(),
            "requested_end": self.requested_end.isoformat(),
            "interval": self.interval,
            "auto_adjust": self.auto_adjust,
            "first_date": self.first_date.isoformat() if self.first_date else None,
            "last_date": self.last_date.isoformat() if self.last_date else None,
            "row_count": self.row_count,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "cleaning": dict(self.cleaning),
        }
        if artifact_path:
            payload["artifact_path"] = artifact_path
        return payload


def build_market_snapshot(
    ticker: str,
    raw: pd.DataFrame,
    *,
    requested_start: date,
    requested_end: date,
    min_complete_bars: int = DEFAULT_MIN_COMPLETE_BARS,
    now: datetime | None = None,
) -> MarketSnapshot:
    normalized = _normalise_ticker(ticker)
    history, cleaning = clean_ohlcv_history(
        raw, ticker=normalized, as_of=requested_end, now=now
    )
    # Include the canonical index name in the content hash even when no bars
    # survived cleaning, so persisted empty artifacts round-trip identically.
    history.index.name = "Date"
    data_hash = history_data_hash(history)
    row_count = len(history)
    reasons: list[str] = []
    if history.empty:
        reasons.append("empty_history")
    if row_count < int(min_complete_bars):
        reasons.append("insufficient_complete_bars")
    status = "ready" if not reasons else "insufficient_data"
    first_date = history.index[0].date() if row_count else None
    last_date = history.index[-1].date() if row_count else None
    identity = "|".join(
        (
            SNAPSHOT_CONTRACT_VERSION,
            normalized,
            requested_start.isoformat(),
            requested_end.isoformat(),
            SNAPSHOT_INTERVAL,
            str(SNAPSHOT_AUTO_ADJUST),
            data_hash,
        )
    )
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    snapshot_id = (
        f"{normalized}-{last_date.isoformat() if last_date else 'empty'}-"
        f"{identity_hash}"
    )
    return MarketSnapshot(
        ticker=normalized,
        history=history,
        requested_start=requested_start,
        requested_end=requested_end,
        data_hash=data_hash,
        snapshot_id=snapshot_id,
        first_date=first_date,
        last_date=last_date,
        row_count=row_count,
        status=status,
        reason_codes=tuple(reasons),
        cleaning=cleaning,
    )


def build_market_snapshots(
    raw: pd.DataFrame,
    tickers: Sequence[str],
    *,
    requested_start: date,
    requested_end: date,
    min_complete_bars: int = DEFAULT_MIN_COMPLETE_BARS,
    now: datetime | None = None,
) -> dict[str, MarketSnapshot]:
    normalized_tickers = [_normalise_ticker(ticker) for ticker in tickers]
    return {
        ticker: build_market_snapshot(
            ticker,
            raw,
            requested_start=requested_start,
            requested_end=requested_end,
            min_complete_bars=min_complete_bars,
            now=now,
        )
        for ticker in normalized_tickers
    }


def snapshots_to_multiindex(
    snapshots: Mapping[str, MarketSnapshot], *, ready_only: bool = True
) -> pd.DataFrame:
    frames = {
        f"{snapshot.ticker}.JK": snapshot.history_copy()
        for snapshot in snapshots.values()
        if not snapshot.history.empty and (snapshot.is_ready or not ready_only)
    }
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


def resample_daily_to_weekly(history: pd.DataFrame) -> pd.DataFrame:
    """Build weekly OHLCV from the exact daily bars instead of refetching."""
    if history is None or history.empty:
        return pd.DataFrame(columns=_OHLCV_COLUMNS)
    weekly = history.loc[:, list(_OHLCV_COLUMNS)].resample("W-FRI").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    weekly = weekly.dropna(subset=["Open", "High", "Low", "Close"])
    weekly.index.name = "Date"
    return weekly.astype("float64")


def download_market_snapshot(
    ticker: str,
    *,
    as_of: date | None = None,
    lookback_calendar_days: int = DEFAULT_LOOKBACK_CALENDAR_DAYS,
    min_complete_bars: int = DEFAULT_MIN_COMPLETE_BARS,
    downloader: Callable[..., pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> MarketSnapshot:
    """Download one daily snapshot with explicit provider arguments."""
    if downloader is None:
        import yfinance as yf

        downloader = yf.download
    start, end = snapshot_window(
        as_of, lookback_calendar_days=lookback_calendar_days
    )
    raw = downloader(
        to_yfinance_symbol(ticker),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval=SNAPSHOT_INTERVAL,
        auto_adjust=SNAPSHOT_AUTO_ADJUST,
        progress=False,
        threads=False,
        timeout=30,
    )
    return build_market_snapshot(
        ticker,
        raw,
        requested_start=start,
        requested_end=end,
        min_complete_bars=min_complete_bars,
        now=now,
    )


def save_market_snapshot(snapshot: MarketSnapshot, path: Path) -> Path:
    """Persist a round-trippable gzip JSON artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    history = snapshot.history_copy()
    # Empty provider/IPO snapshots still need the same on-disk schema as ready
    # snapshots. A default empty RangeIndex would otherwise serialize as an
    # "index" column and fail before the audit artifact can be written.
    history.index.name = "Date"
    bars = history.reset_index()
    bars["Date"] = pd.to_datetime(bars["Date"]).dt.strftime("%Y-%m-%d")
    payload = {
        "metadata": snapshot.provenance(),
        "bars": bars.to_dict(orient="records"),
    }
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    return path


def persist_market_snapshots(
    snapshots: Mapping[str, MarketSnapshot], directory: Path
) -> dict[str, Path]:
    """Persist snapshots and a manifest for cross-process handoff."""
    resolved_directory = Path(directory).resolve()
    validated: list[tuple[str, MarketSnapshot, Path]] = []
    for ticker, snapshot in sorted(snapshots.items()):
        normalized_key = normalize_idx_ticker(ticker)
        normalized_snapshot_ticker = normalize_idx_ticker(snapshot.ticker)
        if normalized_key != normalized_snapshot_ticker:
            raise ValueError("Snapshot mapping key does not match snapshot ticker.")
        expected_prefix = f"{normalized_snapshot_ticker}-"
        if not snapshot.snapshot_id.startswith(expected_prefix):
            raise ValueError("Snapshot ID does not match its normalized ticker.")
        path = resolve_within_root(
            resolved_directory,
            f"{snapshot.snapshot_id}.json.gz",
        )
        validated.append((normalized_key, snapshot, path))

    manifest_path = resolve_within_root(resolved_directory, "manifest.json")
    resolved_directory.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    entries: list[dict[str, Any]] = []
    for ticker, snapshot, path in validated:
        save_market_snapshot(snapshot, path)
        paths[ticker] = path
        entries.append(snapshot.provenance(artifact_path=path.name))
    manifest_path.write_text(
        json.dumps(
            {
                "contract_version": SNAPSHOT_CONTRACT_VERSION,
                "snapshots": entries,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return paths


def load_market_snapshot(
    path: Path,
    *,
    expected_snapshot_id: str | None = None,
    expected_data_hash: str | None = None,
) -> MarketSnapshot:
    """Load and integrity-check a persisted cross-process snapshot."""
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    metadata = dict(payload.get("metadata") or {})
    history = pd.DataFrame(payload.get("bars") or [])
    if history.empty:
        history = pd.DataFrame(columns=("Date", *_OHLCV_COLUMNS))
    missing = {"Date", *_OHLCV_COLUMNS} - set(history.columns)
    if missing:
        raise ValueError(f"Snapshot artifact missing columns: {sorted(missing)}")
    history["Date"] = pd.to_datetime(history["Date"], errors="raise")
    history = (
        history.set_index("Date")
        .loc[:, list(_OHLCV_COLUMNS)]
        .astype("float64")
    )
    history.index.name = "Date"
    actual_hash = history_data_hash(history)
    recorded_hash = str(metadata.get("data_hash") or "")
    if actual_hash != recorded_hash:
        raise ValueError(
            f"Snapshot data hash mismatch: recorded={recorded_hash} actual={actual_hash}"
        )
    snapshot_id = str(metadata.get("snapshot_id") or "")
    if expected_snapshot_id and snapshot_id != expected_snapshot_id:
        raise ValueError("Snapshot ID does not match candidate provenance")
    if expected_data_hash and actual_hash != expected_data_hash:
        raise ValueError("Snapshot hash does not match candidate provenance")
    row_count = int(metadata.get("row_count") or 0)
    if row_count != len(history):
        raise ValueError("Snapshot row count does not match artifact bars")
    return MarketSnapshot(
        ticker=_normalise_ticker(metadata.get("ticker", "")),
        history=history,
        requested_start=date.fromisoformat(metadata["requested_start"]),
        requested_end=date.fromisoformat(metadata["requested_end"]),
        data_hash=actual_hash,
        snapshot_id=snapshot_id,
        first_date=(
            date.fromisoformat(metadata["first_date"])
            if metadata.get("first_date")
            else None
        ),
        last_date=(
            date.fromisoformat(metadata["last_date"])
            if metadata.get("last_date")
            else None
        ),
        row_count=row_count,
        status=str(metadata.get("status") or "insufficient_data"),
        reason_codes=tuple(metadata.get("reason_codes") or ()),
        cleaning=dict(metadata.get("cleaning") or {}),
        source=str(metadata.get("source") or "yfinance"),
        interval=str(metadata.get("interval") or SNAPSHOT_INTERVAL),
        auto_adjust=bool(metadata.get("auto_adjust", SNAPSHOT_AUTO_ADJUST)),
        contract_version=str(
            metadata.get("contract_version") or SNAPSHOT_CONTRACT_VERSION
        ),
    )


def candidate_snapshot_provenance(
    snapshot: MarketSnapshot, *, artifact_path: str | None = None
) -> dict[str, Any]:
    """Flat and nested fields consumed by candidate JSON and cache seeders."""
    return {
        "snapshot_id": snapshot.snapshot_id,
        "data_hash": snapshot.data_hash,
        "snapshot_path": artifact_path,
        "market_snapshot": snapshot.provenance(artifact_path=artifact_path),
    }
