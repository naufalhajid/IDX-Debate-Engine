"""
core/regime_gate.py — LangGraph node: IDX regime detection gate

Runs before the scout fan-out in the debate pipeline.  Detects the current
IHSG market regime via IDXRegimeDetector (HMM) and writes:
    regime         — RegimeState fields as plain dict (LangGraph-serializable)
    trading_params — REGIME_RULES[label] dict (position limits, R/R thresholds)
    should_trade   — False when trading_allowed=False or max_concurrent=0

Routing (regime_gate_router):
    should_trade=True  -> "scout_dispatcher" (proceeds to parallel scouts)
    should_trade=False -> "trading_halted"   (skips all LLM work, emits HOLD)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pandas as pd

from core.idx_market_params import REGIME_RULES
from core.regime_hmm import IDXRegimeDetector
from schemas.debate import DebateChamberState

logger = logging.getLogger(__name__)

# Module-level detector singleton — not re-fitted on every pipeline run.
_detector: IDXRegimeDetector | None = None

# Module-level price caches — shared across all tickers in a batch (W5 fix).
# Re-downloaded only after _IHSG_CACHE_TTL elapses, so a 10-ticker batch pays
# one network round-trip rather than ten.  Falls back to stale data on transient
# network failure rather than silently returning should_trade=False for all tickers.
_ihsg_prices_cache: pd.Series | None = None
_ihsg_cache_time: datetime | None = None
_usd_idr_cache: pd.Series | None = None
_usd_idr_cache_time: datetime | None = None
_IHSG_CACHE_TTL = timedelta(hours=4)


def _get_detector() -> IDXRegimeDetector:
    global _detector
    if _detector is None:
        _detector = IDXRegimeDetector()
        if _detector._load_model():
            logger.info("[RegimeGate] Cached model loaded successfully.")
        else:
            logger.info("[RegimeGate] No cached model found — will fit on first predict().")
    return _detector


async def regime_gate_node(state: "DebateChamberState") -> dict:
    """
    Async LangGraph node: fetch IHSG prices and detect current regime.

    On failure falls back to UNKNOWN / should_trade=False so the pipeline
    halts rather than trading blind.
    """
    try:
        import yfinance as yf

        global _ihsg_prices_cache, _ihsg_cache_time, _usd_idr_cache, _usd_idr_cache_time
        now = datetime.now()
        cache_fresh = (
            _ihsg_prices_cache is not None
            and _ihsg_cache_time is not None
            and (now - _ihsg_cache_time) < _IHSG_CACHE_TTL
        )

        if cache_fresh:
            prices = _ihsg_prices_cache
        else:
            try:
                loop = asyncio.get_running_loop()
                raw = await loop.run_in_executor(
                    None,
                    lambda: yf.download(
                        "^JKSE",
                        period="4y",
                        progress=False,
                        auto_adjust=True,
                        timeout=20,
                    ),
                )
                if raw is None or raw.empty:
                    raise ValueError("^JKSE download returned empty DataFrame")
                prices = raw["Close"].squeeze()
                if prices.empty:
                    raise ValueError("^JKSE Close series empty after squeeze")
                _ihsg_prices_cache = prices
                _ihsg_cache_time = now
                logger.info("[RegimeGate] ^JKSE fetched and cached (%d rows).", len(prices))
            except Exception as fetch_exc:
                if _ihsg_prices_cache is not None:
                    logger.warning(
                        "[RegimeGate] ^JKSE fetch failed (%s) — using stale cache from %s.",
                        fetch_exc,
                        _ihsg_cache_time,
                    )
                    prices = _ihsg_prices_cache
                else:
                    raise  # no fallback available — propagate to outer except

        # USD/IDR feature: IDR weakening precedes IHSG drops 1-2 days.
        # Cache lifetime matches IHSG; degrades gracefully to None (3-feature model) on failure.
        usd_idr: pd.Series | None = None
        usd_idr_fresh = (
            _usd_idr_cache is not None
            and _usd_idr_cache_time is not None
            and (now - _usd_idr_cache_time) < _IHSG_CACHE_TTL
        )
        if usd_idr_fresh:
            usd_idr = _usd_idr_cache
        else:
            try:
                _idr_loop = asyncio.get_running_loop()
                raw_idr = await _idr_loop.run_in_executor(
                    None,
                    lambda: yf.download(
                        "IDR=X",
                        period="4y",
                        progress=False,
                        auto_adjust=True,
                        timeout=20,
                    ),
                )
                if raw_idr is not None and not raw_idr.empty:
                    usd_idr = raw_idr["Close"].squeeze()
                    _usd_idr_cache = usd_idr
                    _usd_idr_cache_time = now
                    logger.info("[RegimeGate] IDR=X fetched and cached (%d rows).", len(usd_idr))
                else:
                    usd_idr = _usd_idr_cache
                    logger.warning("[RegimeGate] IDR=X download empty — usd_idr feature disabled.")
            except Exception as idr_exc:
                usd_idr = _usd_idr_cache
                logger.warning(
                    "[RegimeGate] IDR=X fetch failed (%s) — usd_idr feature disabled.", idr_exc
                )

        detector = _get_detector()
        # predict() may trigger CPU-bound HMM fit; run in executor to avoid
        # blocking the event loop (Fix 3).
        _loop = asyncio.get_running_loop()
        _usd = usd_idr
        regime_state = await _loop.run_in_executor(
            None, lambda: detector.predict(prices, usd_idr=_usd)
        )
        rules = detector.get_trading_rules(regime_state)
        should_trade = (
            rules.get("trading_allowed", False)
            and rules.get("max_concurrent_positions", 0) > 0
        )

        logger.info(
            "[RegimeGate] %s | confidence=%.1f%% | msci_override=%s | trade=%s",
            regime_state.label,
            regime_state.confidence * 100,
            regime_state.msci_override,
            should_trade,
        )

        return {
            "regime": {
                "label": regime_state.label,
                "confidence": regime_state.confidence,
                "probabilities": regime_state.probabilities,
                "msci_override": regime_state.msci_override,
                "training_days": regime_state.training_days,
                "detected_at": regime_state.detected_at,
                "notes": regime_state.notes,
            },
            "trading_params": dict(rules),
            "should_trade": should_trade,
        }

    except Exception as exc:
        logger.error(
            "[RegimeGate] Regime detection failed (%s) -- defaulting to UNKNOWN/no-trade.",
            exc,
        )
        return {
            "regime": {
                "label": "UNKNOWN",
                "confidence": 0.0,
                "probabilities": {},
                "msci_override": False,
                "training_days": 0,
                "detected_at": datetime.now().isoformat(),
                "notes": f"Detection failed: {exc}",
            },
            "trading_params": dict(REGIME_RULES["UNKNOWN"]),
            "should_trade": False,
        }


def regime_gate_router(state: "DebateChamberState") -> str:
    """
    Route after regime_gate_node.

    Returns "scout_dispatcher" to proceed to scouts, or "trading_halted"
    to skip all LLM work and emit a regime-halted HOLD verdict.
    """
    if not state.get("should_trade", False):
        label = state.get("regime", {}).get("label", "UNKNOWN")
        logger.info("[RegimeGate] Trading halted -- regime=%s", label)
        return "trading_halted"
    return "scout_dispatcher"
