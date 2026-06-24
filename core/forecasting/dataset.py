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
      Fundamental: pe_ratio, pb_ratio, ocf_price_pct (NaN — DB source needed)
      Regime: regime (str categorical)
      Forward: close_t{h} for each horizon in horizons (removed by build_labels)
    """

    def build(
        self,
        tickers: list[str],
        start: date,
        end: date,
        horizons: tuple[int, ...] = (5, 10, 20),
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for ticker in tickers:
            try:
                df = self._build_ticker(ticker, start, end, horizons)
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

        # Fundamental placeholders (filled from DB by caller if needed)
        df["pe_ratio"] = np.nan
        df["pb_ratio"] = np.nan
        df["ocf_price_pct"] = np.nan   # flag added by service when missing

        # Regime feature
        df["regime"] = self._get_regime()

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

        return df

    def _get_regime(self) -> str:
        try:
            from core.regime import get_current_regime
            return get_current_regime()
        except Exception:
            return "NORMAL"


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
