"""DatasetBuilder: OHLCV + technicals → point-in-time feature DataFrame."""
from __future__ import annotations

import contextlib
import io
import logging
import warnings
from datetime import date, timedelta
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    pass

_YF_TIMEOUT_S: int = 15
_MIN_BARS: int = 60
_REGIME_HIGH_THRESHOLD: float = 0.02
_REGIME_LOW_THRESHOLD: float = 0.01
_REGIME_DEFENSIVE_WEEKLY_DROP: float = 0.05
_REGIME_RECOVERY_WEEKLY_BOUNCE: float = 0.10


def _get_yf():
    import yfinance as yf
    return yf


def _download_ohlcv(
    ticker: str,
    start: date,
    end: date,
) -> pd.DataFrame:
    """Download full OHLCV from yfinance with .JK suffix for IDX tickers."""
    symbol = ticker.upper()
    if not symbol.endswith(".JK"):
        symbol = f"{symbol}.JK"

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
    ) -> pd.DataFrame:
        # Download IHSG once for the full window — shared across all tickers
        ihsg_regimes = _compute_ihsg_regimes(start, end)

        frames: list[pd.DataFrame] = []
        for ticker in tickers:
            try:
                df = self._build_ticker(ticker, start, end, horizons, ihsg_regimes)
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
    ) -> pd.DataFrame | None:
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

        # Drop rows where any horizon forward price is NaN
        fwd_cols = [f"close_t{h}" for h in horizons]
        df = df.dropna(subset=fwd_cols)

        if df.empty:
            return None

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

        # Flatten MultiIndex columns if present
        if hasattr(raw.columns, "nlevels") and raw.columns.nlevels > 1:
            raw.columns = [col[0] if isinstance(col, tuple) else col for col in raw.columns.to_flat_index()]
        raw.columns = [str(c).lower() for c in raw.columns]

        close = raw["close"].squeeze().dropna().sort_index()
        if len(close) < 20:
            return None

        # Rolling 20-day realized volatility (daily std of returns)
        daily_ret = close.pct_change()
        rolling_vol = daily_ret.rolling(20).std()
        ret5d = close.pct_change(5)
        ma20 = close.rolling(20).mean()
        ma50 = close.rolling(50).mean()
        ma200 = close.rolling(200).mean()

        defensive = (ret5d <= -_REGIME_DEFENSIVE_WEEKLY_DROP) | (
            (close < ma20) & (close < ma50) & (close < ma200)
        )
        # Fill NaN from rolling windows as False
        defensive = defensive.fillna(False)
        vol_high = rolling_vol >= _REGIME_HIGH_THRESHOLD

        recovery = (
            ~defensive
            & vol_high
            & (ret5d >= _REGIME_RECOVERY_WEEKLY_BOUNCE)
        ).fillna(False)
        high = (~defensive & ~recovery & vol_high).fillna(False)
        low = (
            ~defensive & ~recovery & ~high & (rolling_vol < _REGIME_LOW_THRESHOLD)
        ).fillna(False)

        out = pd.DataFrame({
            "regime_defensive": defensive.astype(int),
            "regime_recovery": recovery.astype(int),
            "regime_high": high.astype(int),
            "regime_low": low.astype(int),
        }, index=close.index)

        # Restrict to requested window
        out.index = pd.to_datetime(out.index)
        out = out[(out.index.date >= start) & (out.index.date <= end)]  # type: ignore[operator]
        return out if not out.empty else None

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
