"""
core/regime_gate.py — LangGraph node: IDX regime detection gate

Runs before the scout fan-out in the debate pipeline.  Detects the current
IHSG market regime via IDXRegimeDetector (HMM) and writes:
    hmm_regime       — HMM diagnostic only
    regime_context   — canonical execution authority and provenance
    trading_params   — policy derived only from execution_regime
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
from core.execution_regime import resolve_execution_regime
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


async def detect_hmm_regime() -> dict:
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
        logger.info(
            "[RegimeGate] HMM %s | confidence=%.1f%% | msci_override=%s",
            regime_state.label,
            regime_state.confidence * 100,
            regime_state.msci_override,
        )

        return {
            "label": regime_state.label,
            "confidence": regime_state.confidence,
            "probabilities": regime_state.probabilities,
            "msci_override": regime_state.msci_override,
            "training_days": regime_state.training_days,
            "detected_at": regime_state.detected_at,
            "notes": regime_state.notes,
        }

    except Exception as exc:
        logger.error(
            "[RegimeGate] Regime detection failed (%s) -- defaulting to UNKNOWN/no-trade.",
            exc,
        )
        return {
            "label": "UNKNOWN",
            "confidence": 0.0,
            "probabilities": {},
            "msci_override": False,
            "training_days": 0,
            "detected_at": datetime.now().isoformat(),
            "notes": f"Detection failed: {exc}",
        }


async def regime_gate_node(state: "DebateChamberState") -> dict:
    """Resolve HMM and rule-based diagnostics into one execution authority."""
    precomputed_context = state.get("regime_context")
    precomputed_hmm = state.get("hmm_regime")
    if (
        isinstance(precomputed_context, dict)
        and precomputed_context.get("execution_regime")
        and isinstance(precomputed_hmm, dict)
    ):
        context = dict(precomputed_context)
        hmm_state = dict(precomputed_hmm)
    else:
        hmm_state = await detect_hmm_regime()
        metadata = state.get("metadata") or {}
        rule_snapshot = (
            metadata.get("rule_regime_snapshot")
            or metadata.get("market_regime")
        )
        if rule_snapshot is None and metadata.get("regime"):
            rule_snapshot = {
                "regime": metadata.get("regime"),
                "volatility_regime": metadata.get("volatility_regime"),
            }
        context = resolve_execution_regime(
            rule_snapshot=rule_snapshot,
            hmm_state=hmm_state,
        )

    execution_params = dict(
        context.get("execution_params") or REGIME_RULES["UNKNOWN"]
    )
    should_trade = bool(
        execution_params.get("trading_allowed", False)
        and execution_params.get("max_concurrent_positions", 0) > 0
    )
    execution_regime = str(
        context.get("execution_regime") or "UNKNOWN"
    ).upper()
    logger.info(
        "[RegimeGate] execution=%s reason=%s trend=%s volatility=%s trade=%s",
        execution_regime,
        context.get("execution_regime_reason"),
        (context.get("trend_regime") or {}).get("label"),
        context.get("volatility_regime"),
        should_trade,
    )
    return {
        "hmm_regime": hmm_state,
        "regime_context": context,
        "trend_regime": context.get("trend_regime"),
        "volatility_regime": context.get("volatility_regime"),
        "execution_regime": execution_regime,
        "execution_regime_reason": context.get("execution_regime_reason"),
        "trading_params": execution_params,
        "should_trade": should_trade,
    }


def regime_gate_router(state: "DebateChamberState") -> str:
    """
    Route after regime_gate_node.

    Returns "scout_dispatcher" to proceed to scouts, or "trading_halted"
    to skip all LLM work and emit a regime-halted HOLD verdict.
    """
    if not state.get("should_trade", False):
        label = str(state.get("execution_regime") or "UNKNOWN")
        logger.info("[RegimeGate] Trading halted -- regime=%s", label)
        return "trading_halted"
    return "scout_dispatcher"
