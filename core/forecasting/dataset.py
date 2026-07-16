"""DatasetBuilder: OHLCV + technicals → point-in-time feature DataFrame."""
from __future__ import annotations

import contextlib
import io
import logging
import warnings
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Callable, Mapping

import numpy as np
import pandas as pd

from utils.ticker import normalize_idx_ticker, normalize_idx_tickers, to_yfinance_symbol
from utils.market_snapshot import validate_market_snapshot_integrity

if TYPE_CHECKING:
    from utils.market_snapshot import MarketSnapshot

_YF_TIMEOUT_S: int = 15
_MIN_BARS: int = 60
_REGIME_HIGH_THRESHOLD: float = 0.02
_REGIME_LOW_THRESHOLD: float = 0.01
_REGIME_DEFENSIVE_WEEKLY_DROP: float = 0.05
_REGIME_RECOVERY_WEEKLY_BOUNCE: float = 0.10
_IHSG_REQUIRED_PREHISTORY_CALENDAR_DAYS: int = 280
_IHSG_DEFAULT_LOOKBACK_CALENDAR_DAYS: int = 1_100
_IHSG_MIN_COMPLETE_BARS: int = 400


@dataclass(frozen=True)
class ForecastDatasetSplit:
    """Point-in-time model inputs with an explicit train/inference boundary."""

    training_features: pd.DataFrame
    inference_features: pd.DataFrame


def split_forecast_dataset(
    dataset: pd.DataFrame,
    *,
    horizon: int,
) -> ForecastDatasetSplit:
    """Split a materialized dataset into known-label history and one live row.

    ``training_features`` contains only rows whose selected-horizon future
    close is already known. ``inference_features`` contains exactly the latest
    row whose future close is unknown, so it cannot accidentally be fitted.
    """
    forward_column = f"close_t{int(horizon)}"
    if forward_column not in dataset.columns:
        raise ValueError(f"Missing forward price column: {forward_column}")

    training = dataset.dropna(subset=[forward_column]).copy(deep=True)
    inference = dataset.loc[dataset[forward_column].isna()].tail(1).copy(deep=True)
    return ForecastDatasetSplit(
        training_features=training,
        inference_features=inference,
    )


def _get_yf():
    import yfinance as yf
    return yf


def _download_ohlcv(
    ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Download full OHLCV from yfinance with .JK suffix for IDX tickers."""
    symbol = to_yfinance_symbol(ticker)

    yf_log = logging.getLogger("yfinance")
    prev_disabled = yf_log.disabled
    try:
        yf_log.disabled = True
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                frame = _get_yf().download(
                    symbol,
                    start=start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    progress=False,
                    auto_adjust=True,
                    threads=False,
                    timeout=_YF_TIMEOUT_S,
                )
    finally:
        yf_log.disabled = prev_disabled

    if frame is None or frame.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns (yfinance >= 0.2.x)
    if hasattr(frame.columns, "nlevels") and frame.columns.nlevels > 1:
        frame.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in frame.columns.to_flat_index()
        ]

    frame.columns = [str(c).lower() for c in frame.columns]
    frame.index = pd.to_datetime(frame.index)
    frame = frame.sort_index()
    return frame


class DatasetBuilder:
    """Build a point-in-time feature DataFrame for the forecasting models.

    Returns a (ticker, date) MultiIndex DataFrame with columns:
      Technical: rsi14, atr_pct, log_return, volume_surge (NaN when unavailable)
      Fundamental: pe_ratio, pb_ratio, ocf_price_pct (NaN when DB has no coverage)
      Regime: regime_high, regime_defensive, regime_recovery, regime_low (one-hot)
      Forward: close_t{h} for each horizon in horizons (removed by build_labels)
    """

    def build(
        self,
        tickers: list[str],
        start: date,
        end: date,
        horizons: tuple[int, ...] = (5, 10, 20),
        snapshots: Mapping[str, "MarketSnapshot"] | None = None,
        *,
        ihsg_snapshot: "MarketSnapshot | None" = None,
        include_unlabeled_tail: bool = False,
    ) -> pd.DataFrame:
        normalized_tickers = normalize_idx_tickers(tickers)
        normalized_snapshots: dict[str, MarketSnapshot] = {}
        if snapshots:
            for key, snapshot in snapshots.items():
                normalized_key = normalize_idx_ticker(key)
                normalized_snapshot_ticker = normalize_idx_ticker(snapshot.ticker)
                if normalized_key != normalized_snapshot_ticker:
                    raise ValueError(
                        "Forecast snapshot mapping key does not match snapshot ticker."
                    )
                validate_market_snapshot_integrity(
                    snapshot,
                    expected_ticker=normalized_key,
                )
                normalized_snapshots[normalized_key] = snapshot

        # Download IHSG once for the full window — shared across all tickers
        if ihsg_snapshot is not None:
            _validate_ihsg_snapshot(ihsg_snapshot, start=start, end=end)
            ihsg_regimes = _compute_ihsg_regimes_from_history(
                ihsg_snapshot.history_copy(),
                start,
                end,
            )
            if ihsg_regimes is None or ihsg_regimes.empty:
                raise ValueError(
                    "Frozen IHSG snapshot could not produce regime features."
                )
        else:
            # Legacy/provider mode. Frozen pipeline calls always inject a
            # snapshot and therefore never reach this network path.
            ihsg_regimes = _compute_ihsg_regimes(start, end)

        frames: list[pd.DataFrame] = []
        for ticker in normalized_tickers:
            try:
                snapshot = normalized_snapshots.get(ticker)
                df = self._build_ticker(
                    ticker,
                    start,
                    end,
                    horizons,
                    ihsg_regimes,
                    snapshot=snapshot,
                    include_unlabeled_tail=include_unlabeled_tail,
                )
                if df is not None and len(df) >= _MIN_BARS:
                    frames.append(df)
            except Exception:
                pass

        if not frames:
            return pd.DataFrame()

        return pd.concat(frames).sort_index()

    def _build_ticker(
        self,
        ticker: str,
        start: date,
        end: date,
        horizons: tuple[int, ...],
        ihsg_regimes: pd.DataFrame | None = None,
        snapshot: "MarketSnapshot | None" = None,
        *,
        include_unlabeled_tail: bool = False,
    ) -> pd.DataFrame | None:
        if snapshot is not None:
            raw = snapshot.history_copy()
            raw.columns = [str(column).lower() for column in raw.columns]
            raw.index = pd.to_datetime(raw.index)
            raw = raw.sort_index()
        else:
            raw = _download_ohlcv(ticker, start - timedelta(days=100), end)
        if raw.empty or len(raw) < _MIN_BARS:
            return None

        if "close" not in raw.columns:
            return None

        df = raw[["close"]].copy()

        # Add open/high/low/volume when available (auto_adjust=True may drop open)
        for col in ("open", "high", "low", "volume"):
            if col in raw.columns:
                df[col] = raw[col]

        # Technical features
        df = self._add_technicals(df)

        # Fundamental features — point-in-time join from DB
        _fill_fundamentals(df, ticker)

        # Regime one-hot from IHSG rolling history (time-varying, not current snapshot)
        _fill_regime_onehot(df, ihsg_regimes)

        # Forward close prices for label generation
        for h in horizons:
            df[f"close_t{h}"] = df["close"].shift(-h)

        # Restrict to [start, end] after adding forward prices
        df.index = pd.to_datetime(df.index)
        df = df[(df.index.date >= start) & (df.index.date <= end)]  # type: ignore[operator]

        # Label-complete history is used for model fitting and validation.
        # The opt-in tail preserves the newest rows whose future outcome is not
        # known yet so ForecastingService can use the latest one for inference.
        fwd_cols = [f"close_t{h}" for h in horizons]
        label_complete = df.dropna(subset=fwd_cols)
        if len(label_complete) < _MIN_BARS:
            return None
        if not include_unlabeled_tail:
            df = label_complete

        df["ticker"] = ticker.upper()
        df["date"] = pd.to_datetime(df.index).date
        df = df.set_index(["ticker", "date"])

        return df

    def _add_technicals(self, df: pd.DataFrame) -> pd.DataFrame:
        close = df["close"]

        # Log return
        df["log_return"] = np.log(close / close.shift(1))

        # RSI-14
        df["rsi14"] = _compute_rsi(close, period=14)

        # ATR as fraction of price
        if "high" in df.columns and "low" in df.columns:
            df["atr14"] = _compute_atr(df["high"], df["low"], close, period=14)
            df["atr_pct"] = df["atr14"] / close
        else:
            df["atr_pct"] = close.pct_change().abs().rolling(14).mean()

        # Volume surge (vol / 20-day avg vol)
        if "volume" in df.columns:
            vol_ma = df["volume"].rolling(20).mean()
            df["volume_surge"] = df["volume"] / vol_ma.replace(0, np.nan)
        else:
            df["volume_surge"] = np.nan

        # Multi-horizon momentum — top features for XGBoost on emerging markets
        for lag in (5, 10, 20):
            df[f"return_{lag}d"] = close.pct_change(lag)
        df["price_above_ma20"] = (close > close.rolling(20).mean()).astype(int)

        return df


def _compute_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
    delta = prices.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def _compute_ihsg_regimes_from_history(
    history: pd.DataFrame,
    start: date,
    end: date,
) -> pd.DataFrame | None:
    """Compute causal, per-date regime features from frozen IHSG bars."""

    if history is None or history.empty:
        return None
    raw = history.copy(deep=True)
    if hasattr(raw.columns, "nlevels") and raw.columns.nlevels > 1:
        raw.columns = [
            col[0] if isinstance(col, tuple) else col
            for col in raw.columns.to_flat_index()
        ]
    raw.columns = [str(column).lower() for column in raw.columns]
    if "close" not in raw.columns:
        return None
    raw.index = pd.to_datetime(raw.index, errors="coerce")
    raw = raw[~raw.index.isna()].sort_index(kind="stable")
    raw = raw[~raw.index.duplicated(keep="last")]
    raw = raw[raw.index.date <= end]
    close = raw["close"].squeeze().dropna().sort_index()
    if len(close) < 20:
        return None

    daily_ret = close.pct_change()
    rolling_vol = daily_ret.rolling(20).std()
    ret5d = close.pct_change(5)
    ma20 = close.rolling(20).mean()
    ma50 = close.rolling(50).mean()
    ma200 = close.rolling(200).mean()

    defensive = (ret5d <= -_REGIME_DEFENSIVE_WEEKLY_DROP) | (
        (close < ma20) & (close < ma50) & (close < ma200)
    )
    defensive = defensive.fillna(False)
    vol_high = rolling_vol >= _REGIME_HIGH_THRESHOLD
    recovery = (
        ~defensive
        & vol_high
        & (ret5d >= _REGIME_RECOVERY_WEEKLY_BOUNCE)
    ).fillna(False)
    high = (~defensive & ~recovery & vol_high).fillna(False)
    low = (
        ~defensive
        & ~recovery
        & ~high
        & (rolling_vol < _REGIME_LOW_THRESHOLD)
    ).fillna(False)

    out = pd.DataFrame(
        {
            "regime_defensive": defensive.astype(int),
            "regime_recovery": recovery.astype(int),
            "regime_high": high.astype(int),
            "regime_low": low.astype(int),
        },
        index=close.index,
    )
    out.index = pd.to_datetime(out.index)
    out = out[(out.index.date >= start) & (out.index.date <= end)]  # type: ignore[operator]
    return out if not out.empty else None


def _validate_ihsg_snapshot(
    snapshot: "MarketSnapshot",
    *,
    start: date,
    end: date,
) -> None:
    """Fail closed before ticker processing when benchmark data drifts."""

    validate_market_snapshot_integrity(snapshot, expected_ticker="IHSG")
    if normalize_idx_ticker(snapshot.ticker) != "IHSG":
        raise ValueError("IHSG snapshot ticker must be IHSG.")
    if snapshot.requested_end != end or snapshot.last_date != end:
        raise ValueError("IHSG snapshot as-of does not match forecast as-of.")
    history = snapshot.history_copy()
    if history.empty:
        raise ValueError("IHSG snapshot history is empty.")
    dates = pd.to_datetime(history.index, errors="coerce")
    if dates.isna().any() or dates.max().date() > end:
        raise ValueError("IHSG snapshot contains invalid or future-dated bars.")
    required_start = start - timedelta(
        days=_IHSG_REQUIRED_PREHISTORY_CALENDAR_DAYS
    )
    if dates.min().date() > required_start:
        raise ValueError("IHSG snapshot has insufficient prehistory for MA200.")
    close = pd.to_numeric(history.get("Close"), errors="coerce")
    complete_pre_start = close[
        (dates.date < start) & np.isfinite(close.to_numpy(dtype=float))
    ]
    if complete_pre_start.index.nunique() < 200:
        raise ValueError(
            "IHSG snapshot needs at least 200 complete sessions before start."
        )


def download_ihsg_snapshot(
    *,
    as_of: date,
    lookback_calendar_days: int = _IHSG_DEFAULT_LOOKBACK_CALENDAR_DAYS,
    downloader: Callable[..., pd.DataFrame] | None = None,
    now: datetime | None = None,
) -> "MarketSnapshot":
    """Download one explicit ``^JKSE`` snapshot for frozen forecast features."""

    from utils.market_snapshot import build_market_snapshot

    if downloader is None:
        downloader = _get_yf().download
    requested_start = as_of - timedelta(days=int(lookback_calendar_days))
    raw = downloader(
        "^JKSE",
        start=requested_start.isoformat(),
        end=(as_of + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False,
        timeout=_YF_TIMEOUT_S,
    )
    if raw is None:
        raw = pd.DataFrame()
    if isinstance(getattr(raw, "columns", None), pd.MultiIndex):
        raw = raw.copy()
        raw.columns = [
            column[0] if isinstance(column, tuple) else column
            for column in raw.columns.to_flat_index()
        ]
    return build_market_snapshot(
        "IHSG",
        raw,
        requested_start=requested_start,
        requested_end=as_of,
        min_complete_bars=_IHSG_MIN_COMPLETE_BARS,
        now=now,
    )


def _compute_ihsg_regimes(start: date, end: date) -> pd.DataFrame | None:
    """Download ^JKSE and compute a per-date one-hot regime DataFrame.

    Regime logic mirrors core/regime.py:
      DEFENSIVE: 5d drop ≤ -5% OR close < MA20 AND MA50 AND MA200
      RECOVERY:  HIGH vol + NOT defensive + 5d bounce ≥ +10%
      HIGH:      rolling20_std ≥ 2%
      LOW:       rolling20_std < 1%
      NORMAL:    otherwise (baseline — all columns zero)
    """
    try:
        # Need extra lookback for MA200
        extra_start = start - timedelta(days=280)
        with (
            contextlib.redirect_stderr(io.StringIO()),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            yf = _get_yf()
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                raw = yf.download(
                    "^JKSE",
                    start=extra_start.isoformat(),
                    end=(end + timedelta(days=1)).isoformat(),
                    progress=False,
                    auto_adjust=True,
                    threads=False,
                    timeout=_YF_TIMEOUT_S,
                )
        if raw is None or getattr(raw, "empty", True):
            return None

        return _compute_ihsg_regimes_from_history(raw, start, end)

    except Exception:
        return None


def _fill_regime_onehot(df: pd.DataFrame, ihsg_regimes: pd.DataFrame | None) -> None:
    """Add regime one-hot columns to df in-place. Falls back to all zeros."""
    regime_cols = ["regime_defensive", "regime_recovery", "regime_high", "regime_low"]
    if ihsg_regimes is None or ihsg_regimes.empty:
        for col in regime_cols:
            df[col] = 0
        return

    df.index = pd.to_datetime(df.index)
    ihsg_regimes.index = pd.to_datetime(ihsg_regimes.index)

    # Left-join on date; forward-fill gaps (weekends, holidays)
    joined = df[[]].join(ihsg_regimes, how="left").ffill()
    for col in regime_cols:
        df[col] = joined[col].fillna(0).astype(int) if col in joined.columns else 0


def _fill_fundamentals(df: pd.DataFrame, ticker: str) -> None:
    """Point-in-time join of DB fundamental snapshot into df in-place.

    For each OHLCV row date, uses the latest DB row whose created_at <= row date.
    Rows prior to the first DB entry remain NaN (→ 0 via fillna in feature frame).
    ocf_price_pct = (cash_from_operations_ttm / shares_outstanding) / close_price
    """
    df["pe_ratio"] = np.nan
    df["pb_ratio"] = np.nan
    df["ocf_price_pct"] = np.nan

    try:
        from db.session import get_session
        from db.models.fundamental import Fundamental
        from db.models.stock import Stock
        from sqlalchemy import select
        from sqlalchemy.orm import joinedload

        with get_session() as session:
            stmt = (
                select(Fundamental)
                .options(
                    joinedload(Fundamental.current_valuation),
                    joinedload(Fundamental.cash_flow_statement),
                    joinedload(Fundamental.stat),
                )
                .join(Fundamental.stock)
                .where(Stock.ticker == ticker.upper())
                .order_by(Fundamental.created_at)
            )
            rows = session.execute(stmt).scalars().all()

            # Build snapshot list inside the session while objects are still attached
            snapshots: list[tuple[date, float | None, float | None, float | None, float | None]] = []
            for row in rows:
                snap_date = row.created_at.date()
                cv = row.current_valuation
                cf = row.cash_flow_statement
                st = row.stat
                pe = float(cv.current_pe_ratio_ttm) if cv and cv.current_pe_ratio_ttm else None
                pb = float(cv.current_price_to_book_value) if cv and cv.current_price_to_book_value else None
                ocf = float(cf.cash_from_operations_ttm) if cf and cf.cash_from_operations_ttm else None
                shares = float(st.current_share_outstanding) if st and st.current_share_outstanding else None
                snapshots.append((snap_date, pe, pb, ocf, shares))

        if not snapshots:
            return

        snap_dates = [s[0] for s in snapshots]
        snap_pe = [s[1] for s in snapshots]
        snap_pb = [s[2] for s in snapshots]
        snap_ocf = [s[3] for s in snapshots]
        snap_shares = [s[4] for s in snapshots]

        df.index = pd.to_datetime(df.index)
        for idx, row_date in enumerate(pd.to_datetime(df.index).date):
            # Find last snapshot with created_at <= row_date
            best = -1
            for i, sd in enumerate(snap_dates):
                if sd <= row_date:
                    best = i
                else:
                    break
            if best < 0:
                continue
            df.iloc[idx, df.columns.get_loc("pe_ratio")] = snap_pe[best]
            df.iloc[idx, df.columns.get_loc("pb_ratio")] = snap_pb[best]
            ocf = snap_ocf[best]
            shares = snap_shares[best]
            close_val = float(df.iloc[idx]["close"])
            if ocf and shares and shares > 0 and close_val > 0:
                df.iloc[idx, df.columns.get_loc("ocf_price_pct")] = (ocf / shares) / close_val

    except Exception:
        pass
