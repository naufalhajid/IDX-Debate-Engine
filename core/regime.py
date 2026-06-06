"""
core/regime.py — Market Regime Detector berbasis IHSG realized volatility.

Menggunakan ^JKSE 20-day realized volatility (daily std of returns) sebagai
proxy VIX — karena IHSG tidak memiliki volatility index resmi.

Regime classification:
  HIGH   : daily_std >= REGIME_VOLATILITY_HIGH_THRESHOLD (default 2%)
  NORMAL : 1% <= daily_std < 2%
  LOW    : daily_std < REGIME_VOLATILITY_LOW_THRESHOLD (default 1%)

Failure mode:
  Jika fetch_ihsg_volatility() gagal (timeout, rate-limit, dll.),
  volatility dikembalikan sebagai None dan regime di-set ke NORMAL
  sebagai safe fallback. Pipeline tidak dihentikan.

Regime effects (via get_regime_params()) — nilai di bawah adalah default,
bisa di-override lewat env/settings (REGIME_HIGH_* / REGIME_LOW_*):
  HIGH   → top_n=2, rpm_limit=5,  rr_cap=4.0, min_conviction=0.45
  NORMAL → defaults (tidak ada override)
  LOW    → top_n=5, rpm_limit=15, rr_cap=6.0, min_conviction=0.20
"""

from __future__ import annotations

import asyncio
from typing import Literal

from core.settings import settings
from utils.logger_config import logger

RegimeType = Literal["HIGH", "NORMAL", "LOW"]


def _get_yfinance():
    import yfinance as yf

    return yf


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

        close = df["Close"].squeeze().dropna()
        returns = close.pct_change().dropna()
        if len(returns) < lookback_days:
            logger.warning(
                f"[Regime] yfinance ^JKSE: data return terlalu sedikit "
                f"({len(returns)}/{lookback_days})."
            )
            return None

        returns = returns.tail(lookback_days)
        vol = float(returns.std())
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
) -> RegimeType:
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
    if regime == "HIGH":
        return {
            "top_n_selection": settings.REGIME_HIGH_TOP_N,
            "rpm_limit": settings.REGIME_HIGH_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_HIGH_RR_CAP,
            "min_conviction_override": settings.REGIME_HIGH_MIN_CONVICTION,
        }

    if regime == "LOW":
        return {
            "top_n_selection": settings.REGIME_LOW_TOP_N,
            "rpm_limit": settings.REGIME_LOW_RPM_LIMIT,
            "rr_normalization_cap": settings.REGIME_LOW_RR_CAP,
            "min_conviction_override": settings.REGIME_LOW_MIN_CONVICTION,
        }

    return {}  # NORMAL — no overrides
