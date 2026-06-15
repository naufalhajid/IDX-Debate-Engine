"""
core/regime.py — Market Regime Detector berbasis IHSG realized volatility.

Menggunakan ^JKSE 20-day realized volatility (daily std of returns) sebagai
proxy VIX — karena IHSG tidak memiliki volatility index resmi.

Regime classification (urutan prioritas):
  DEFENSIVE : 5d drop <= -5% ATAU close < MA20+MA50+MA200
  RECOVERY  : vol HIGH + NOT defensive + 5d return >= +10% (bounce pasca-koreksi)
  HIGH      : daily_std >= REGIME_VOLATILITY_HIGH_THRESHOLD (default 2%)
  NORMAL    : 1% <= daily_std < 2%
  LOW       : daily_std < REGIME_VOLATILITY_LOW_THRESHOLD (default 1%)

Failure mode:
  Jika fetch_ihsg_volatility() gagal (timeout, rate-limit, dll.),
  volatility dikembalikan sebagai None dan regime di-set ke NORMAL
  sebagai safe fallback. Pipeline tidak dihentikan.

Regime effects (via get_regime_params()) — nilai di bawah adalah default,
bisa di-override lewat env/settings:
  DEFENSIVE → top_n=3, rpm_limit=5,  rr_cap=4.0, min_conviction=0.70
  HIGH      → top_n=2, rpm_limit=5,  rr_cap=4.0, min_conviction=0.45
  RECOVERY  → top_n=4, rpm_limit=8,  rr_cap=4.0, min_conviction=0.40
  NORMAL    → defaults (tidak ada override)
  LOW       → top_n=5, rpm_limit=15, rr_cap=6.0, min_conviction=0.20
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from typing import Literal

from core.settings import settings
from utils.logger_config import logger

VolatilityRegimeType = Literal["HIGH", "NORMAL", "LOW"]
RegimeType = Literal["DEFENSIVE", "RECOVERY", "HIGH", "NORMAL", "LOW"]


@dataclass
class RegimeSnapshot:
    regime: RegimeType
    volatility_regime: VolatilityRegimeType
    volatility: float | None
    weekly_return: float | None
    latest_close: float | None
    ma20: float | None
    ma50: float | None
    ma200: float | None
    defensive_triggered: bool
    reasons: list[str]

    def model_dump(self) -> dict:
        return asdict(self)


def _get_yfinance():
    import yfinance as yf

    return yf


def _close_series(df):
    if df is None or getattr(df, "empty", True):
        return None
    try:
        close = df["Close"].squeeze().dropna()
    except Exception:
        return None
    if len(close) == 0:
        return None
    return close


def _realized_volatility(close, lookback_days: int) -> float | None:
    returns = close.pct_change().dropna()
    if len(returns) < lookback_days:
        return None
    return float(returns.tail(lookback_days).std())


async def fetch_ihsg_ohlcv(period_days: int = 320):
    """Fetch IHSG OHLCV data for direction-aware regime detection."""
    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: _get_yfinance().download(
                "^JKSE",
                period=f"{period_days}d",
                progress=False,
                auto_adjust=True,
                timeout=15,
            ),
        )
        if df.empty:
            logger.warning("[Regime] yfinance ^JKSE: DataFrame kosong.")
            return None
        return df
    except Exception as e:
        logger.warning(f"[Regime] Gagal fetch IHSG OHLCV: {e}.")
        return None


def compute_ihsg_snapshot(
    df,
    *,
    lookback_days: int = 20,
    high_threshold: float = 0.02,
    low_threshold: float = 0.01,
    defensive_weekly_drop_threshold: float = 0.05,
    recovery_weekly_threshold: float = 0.10,
) -> RegimeSnapshot:
    """Compute direction-aware market regime from an IHSG price frame."""
    close = _close_series(df)
    if close is None:
        volatility_regime = classify_regime(None, high_threshold, low_threshold)
        return RegimeSnapshot(
            regime=volatility_regime,
            volatility_regime=volatility_regime,
            volatility=None,
            weekly_return=None,
            latest_close=None,
            ma20=None,
            ma50=None,
            ma200=None,
            defensive_triggered=False,
            reasons=["ihsg_data_unavailable_fallback_to_volatility"],
        )

    volatility = _realized_volatility(close, lookback_days)
    volatility_regime = classify_regime(volatility, high_threshold, low_threshold)
    latest_close = float(close.iloc[-1])
    weekly_return = (
        float((close.iloc[-1] / close.iloc[-6]) - 1.0) if len(close) >= 6 else None
    )
    ma20 = float(close.tail(20).mean()) if len(close) >= 20 else None
    ma50 = float(close.tail(50).mean()) if len(close) >= 50 else None
    ma200 = float(close.tail(200).mean()) if len(close) >= 200 else None

    reasons: list[str] = []
    if (
        weekly_return is not None
        and weekly_return <= -defensive_weekly_drop_threshold
    ):
        reasons.append("weekly_return_below_threshold")

    if (
        ma20 is not None
        and ma50 is not None
        and ma200 is not None
        and latest_close < ma20
        and latest_close < ma50
        and latest_close < ma200
    ):
        reasons.append("close_below_ma20_ma50_ma200")

    defensive_triggered = bool(reasons)
    if not defensive_triggered and (
        volatility is None or weekly_return is None or ma200 is None
    ):
        reasons.append("ihsg_data_unavailable_fallback_to_volatility")

    recovery_triggered = (
        not defensive_triggered
        and volatility_regime == "HIGH"
        and weekly_return is not None
        and weekly_return >= recovery_weekly_threshold
    )
    if recovery_triggered:
        reasons.append("recovery_bounce_detected")

    if defensive_triggered:
        final_regime: RegimeType = "DEFENSIVE"
    elif recovery_triggered:
        final_regime = "RECOVERY"
    else:
        final_regime = volatility_regime

    return RegimeSnapshot(
        regime=final_regime,
        volatility_regime=volatility_regime,
        volatility=volatility,
        weekly_return=weekly_return,
        latest_close=latest_close,
        ma20=ma20,
        ma50=ma50,
        ma200=ma200,
        defensive_triggered=defensive_triggered,
        reasons=reasons,
    )


async def detect_market_regime() -> RegimeSnapshot:
    """Fetch IHSG data and classify the final market regime."""
    df = await fetch_ihsg_ohlcv()
    snapshot = compute_ihsg_snapshot(
        df,
        lookback_days=settings.REGIME_VOLATILITY_LOOKBACK_DAYS,
        high_threshold=settings.REGIME_VOLATILITY_HIGH_THRESHOLD,
        low_threshold=settings.REGIME_VOLATILITY_LOW_THRESHOLD,
        defensive_weekly_drop_threshold=settings.REGIME_DEFENSIVE_WEEKLY_DROP_THRESHOLD,
        recovery_weekly_threshold=settings.REGIME_HIGH_RECOVERY_WEEKLY_THRESHOLD,
    )
    logger.info(
        f"[Regime] IHSG regime={snapshot.regime} "
        f"volatility_regime={snapshot.volatility_regime} "
        f"reasons={','.join(snapshot.reasons) or '-'}"
    )
    return snapshot


async def fetch_ihsg_volatility(lookback_days: int = 20) -> float | None:
    """
    Fetch IHSG (^JKSE) realized volatility dari yfinance.

    Dijalankan di executor agar tidak memblokir event loop asyncio.

    Args:
        lookback_days: Jumlah hari lookback untuk menghitung std returns.

    Returns:
        Daily std of returns sebagai float, atau None jika fetch gagal.
    """
    try:
        loop = asyncio.get_running_loop()
        df = await loop.run_in_executor(
            None,
            lambda: _get_yfinance().download(
                "^JKSE",
                period=f"{lookback_days + max(10, lookback_days // 2)}d",
                progress=False,
                auto_adjust=True,
                timeout=15,
            ),
        )

        if df.empty:
            logger.warning("[Regime] yfinance ^JKSE: DataFrame kosong.")
            return None

        close = _close_series(df)
        if close is None:
            logger.warning("[Regime] yfinance ^JKSE: data close tidak tersedia.")
            return None

        vol = _realized_volatility(close, lookback_days)
        if vol is None:
            returns = close.pct_change().dropna()
            logger.warning(
                f"[Regime] yfinance ^JKSE: data return terlalu sedikit "
                f"({len(returns)}/{lookback_days})."
            )
            return None

        logger.info(
            f"[Regime] IHSG realized vol ({lookback_days}d): {vol:.4f} ({vol * 100:.2f}%)"
        )
        return vol

    except Exception as e:
        logger.warning(
            f"[Regime] Gagal fetch IHSG volatility: {e} — fallback ke NORMAL."
        )
        return None


def classify_regime(
    vol: float | None,
    high_threshold: float = 0.02,
    low_threshold: float = 0.01,
) -> VolatilityRegimeType:
    """
    Klasifikasikan market regime berdasarkan realized volatility.

    Failure mode: jika vol = None (fetch gagal), kembalikan "NORMAL"
    sebagai safe default agar pipeline tidak terganggu.
    """
    if vol is None:
        logger.warning(
            "[Regime] Volatility tidak tersedia — fallback ke NORMAL regime."
        )
        return "NORMAL"

    if vol >= high_threshold:
        logger.info(
            f"[Regime] HIGH volatility ({vol * 100:.2f}% >= {high_threshold * 100:.0f}%)"
        )
        return "HIGH"

    if vol < low_threshold:
        logger.info(
            f"[Regime] LOW volatility ({vol * 100:.2f}% < {low_threshold * 100:.0f}%)"
        )
        return "LOW"

    logger.info(f"[Regime] NORMAL volatility ({vol * 100:.2f}%)")
    return "NORMAL"


def get_regime_params(regime: RegimeType) -> dict:
    """
    Kembalikan override parameters berdasarkan market regime.

    Di-merge ke ORCHESTRATOR_CONFIG di main() sebelum pipeline dimulai.
    NORMAL mengembalikan dict kosong — tidak ada override.

    Kunci yang bisa di-override:
      top_n_selection, rpm_limit, rr_normalization_cap, min_conviction_override
    """
    if regime == "DEFENSIVE":
        return {
            "top_n_selection": settings.REGIME_DEFENSIVE_TOP_N,
            "rpm_limit": settings.REGIME_DEFENSIVE_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_DEFENSIVE_MAX_RR_FOR_SCORING,
            "min_conviction_override": settings.REGIME_DEFENSIVE_MIN_CONVICTION,
        }

    if regime == "HIGH":
        return {
            "top_n_selection": settings.REGIME_HIGH_TOP_N,
            "rpm_limit": settings.REGIME_HIGH_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_HIGH_RR_CAP,
            "min_conviction_override": settings.REGIME_HIGH_MIN_CONVICTION,
        }

    if regime == "RECOVERY":
        return {
            "top_n_selection": settings.REGIME_RECOVERY_TOP_N,
            "rpm_limit": settings.REGIME_RECOVERY_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_RECOVERY_RR_CAP,
            "min_conviction_override": settings.REGIME_RECOVERY_MIN_CONVICTION,
        }

    if regime == "LOW":
        return {
            "top_n_selection": settings.REGIME_LOW_TOP_N,
            "rpm_limit": settings.REGIME_LOW_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_LOW_RR_CAP,
            "min_conviction_override": settings.REGIME_LOW_MIN_CONVICTION,
        }

    return {}  # NORMAL — no overrides
