"""Shared trade math helpers for swing-trade setup calculations."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from utils.logger_config import logger


RR_TIERS_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "rr_tiers.yaml"
)
DEFAULT_RR_TIER_NAME = "default"
LARGE_CAP_TIER_NAME = "large_cap"

# Market cap threshold for large-cap classification (IDR). Rp 50 trillion
# captures LQ45-class stocks without pulling in mid-caps.
LARGE_CAP_THRESHOLD_IDR: int = 50_000_000_000_000
LARGE_CAP_RR_MINIMUM: float = 1.4
DEFAULT_RR_MINIMUM: float = 1.62
USER_EXECUTION_RR_FLOOR: float = 2.0

# ATR stops widen in DEFENSIVE/HIGH regimes; R/R minimums scale up to match.
# LOW and NORMAL use no scaling — base thresholds are calibrated for calm markets.
# HMM labels (BULL/SIDEWAYS/BEAR_STRESS) mirror their legacy equivalents so
# callers need not translate before passing the regime string.
REGIME_RR_SCALING: dict[str, float] = {
    "LOW":         1.0,
    "NORMAL":      1.0,
    "HIGH":        1.2,
    "RECOVERY":    1.1,
    "DEFENSIVE":   1.3,
    # HMM 3-state labels (accepted directly)
    "BULL":        1.0,
    "SIDEWAYS":    1.2,
    "BEAR_STRESS": 1.3,
    "UNKNOWN":     1.3,
}


@dataclass(frozen=True)
class RRTierResolution:
    """Resolved R/R threshold metadata for one ticker."""

    ticker: str
    tier_name: str
    tier_label: str
    rr_minimum: float
    source: str
    market_cap_idr: int | None = None


@dataclass(frozen=True)
class RequiredRRResolution:
    """Auditable final R/R requirement for one execution setup."""

    ticker: str
    required_rr: float
    base_rr_minimum: float
    regime_rr_minimum: float
    user_execution_floor: float
    execution_regime: str
    regime_multiplier: float
    tier_name: str
    tier_label: str
    tier_source: str
    market_cap_idr: int | None = None


def calculate_rr(entry_high: float, target: float, stop: float) -> float:
    """Return conservative risk/reward using entry_high as worst-case fill."""
    if stop >= entry_high:
        raise ValueError(
            f"stop ({stop}) must be below entry_high ({entry_high}) to calculate R/R"
        )
    risk = entry_high - stop
    reward = target - entry_high
    return round(reward / risk, 2)


def _get_market_cap_idr(
    ticker: str, yf_info: dict[str, Any] | None = None
) -> int | None:
    """
    Return market cap in IDR from a yfinance info dict.

    Args:
        ticker: Stock ticker symbol.
        yf_info: Cached yfinance ``Ticker.info`` dict, if available upstream.

    Returns:
        Market cap as integer IDR, or ``None`` when unavailable.
    """
    info = yf_info or {}
    market_cap = info.get("marketCap") if isinstance(info, dict) else None

    if (
        isinstance(market_cap, (int, float))
        and not isinstance(market_cap, bool)
        and market_cap > 0
    ):
        return int(market_cap)

    logger.debug(
        f"[RRTier] {ticker}: marketCap unavailable from yfinance info "
        f"(value={market_cap}). Will use static fallback."
    )
    return None


@lru_cache(maxsize=1)
def _load_lq45_tickers() -> set[str]:
    """Load LQ45 ticker list from ``config/rr_tiers.yaml``."""
    try:
        with RR_TIERS_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        tickers = config.get("lq45_tickers", [])
        if not isinstance(tickers, list):
            return set()
        return {str(t).upper().strip() for t in tickers if str(t).strip()}
    except Exception as exc:
        logger.warning(f"[LQ45] Failed to load lq45_tickers: {exc}")
        return set()


def is_lq45_ticker(ticker: str) -> bool:
    """Return True if ticker is in the static LQ45 list from rr_tiers.yaml."""
    return str(ticker or "").upper().strip() in _load_lq45_tickers()


@lru_cache(maxsize=1)
def _load_largecap_fallback() -> set[str]:
    """
    Load fallback large-cap tickers from ``config/rr_tiers.yaml``.

    Returns:
        Uppercase ticker set. Missing or malformed config disables fallback and
        returns an empty set after logging a warning.
    """
    try:
        with RR_TIERS_CONFIG_PATH.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
        if not isinstance(config, dict):
            raise ValueError("rr_tiers.yaml must contain a YAML mapping.")
        tickers = config.get("large_cap_fallback", [])
        if tickers is None:
            return set()
        if not isinstance(tickers, list):
            raise ValueError("large_cap_fallback must be a list.")
        return {
            str(ticker).upper().strip() for ticker in tickers if str(ticker).strip()
        }
    except FileNotFoundError:
        logger.warning(
            "[RRTier] config/rr_tiers.yaml not found. Static fallback disabled; "
            "all tickers without marketCap use DEFAULT threshold."
        )
        return set()
    except Exception as exc:
        logger.warning(f"[RRTier] Failed to load rr_tiers.yaml: {exc}")
        return set()


def get_rr_resolution(
    ticker: str,
    yf_info: dict[str, Any] | None = None,
) -> RRTierResolution:
    """
    Resolve the R/R threshold and source for a ticker.

    Resolution priority:
    1. Cached yfinance marketCap.
    2. Static large-cap fallback list.
    3. Default threshold.
    """
    ticker_upper = str(ticker or "").upper().strip()
    market_cap = _get_market_cap_idr(ticker_upper, yf_info)
    if market_cap is not None:
        if market_cap >= LARGE_CAP_THRESHOLD_IDR:
            resolution = RRTierResolution(
                ticker=ticker_upper,
                tier_name=LARGE_CAP_TIER_NAME,
                tier_label="Large Cap",
                rr_minimum=LARGE_CAP_RR_MINIMUM,
                source="market_cap",
                market_cap_idr=market_cap,
            )
        else:
            resolution = RRTierResolution(
                ticker=ticker_upper,
                tier_name=DEFAULT_RR_TIER_NAME,
                tier_label="Default",
                rr_minimum=DEFAULT_RR_MINIMUM,
                source="market_cap",
                market_cap_idr=market_cap,
            )
        _log_rr_resolution(resolution)
        return resolution

    if ticker_upper in _load_largecap_fallback():
        resolution = RRTierResolution(
            ticker=ticker_upper,
            tier_name=LARGE_CAP_TIER_NAME,
            tier_label="Large Cap",
            rr_minimum=LARGE_CAP_RR_MINIMUM,
            source="static_fallback",
            market_cap_idr=None,
        )
        _log_rr_resolution(resolution)
        return resolution

    resolution = RRTierResolution(
        ticker=ticker_upper,
        tier_name=DEFAULT_RR_TIER_NAME,
        tier_label="Default",
        rr_minimum=DEFAULT_RR_MINIMUM,
        source="static_default",
        market_cap_idr=None,
    )
    _log_rr_resolution(resolution)
    return resolution


def apply_regime_rr_scaling(base_rr: float, regime: str | None) -> float:
    """Scale a base R/R minimum by the current market regime.

    Returns base_rr unchanged when regime is None, empty, or unknown.
    """
    if not regime:
        return base_rr
    scale = REGIME_RR_SCALING.get(str(regime).upper(), 1.0)
    return round(base_rr * scale, 3)


def get_rr_minimum(
    ticker: str,
    regime: str | None = None,
    yf_info: dict[str, Any] | None = None,
) -> float:
    """Return the minimum acceptable R/R threshold for a ticker.

    Cached yfinance marketCap is preferred. When unavailable, the static YAML
    fallback list is used; otherwise the default threshold is returned.
    Pass ``regime`` to apply regime-aware scaling on top of the tier floor.
    """
    base = get_rr_resolution(ticker, yf_info=yf_info).rr_minimum
    return apply_regime_rr_scaling(base, regime)


def get_required_rr_resolution(
    ticker: str,
    regime: str | None = None,
    yf_info: dict[str, Any] | None = None,
) -> RequiredRRResolution:
    """Return the sole deployable R/R threshold and its provenance.

    The execution contract is intentionally stricter than the historical tier
    calibration: ``max(2.0, tier minimum x execution-regime multiplier)``.
    Keeping all intermediate values makes batch artifacts explain why the
    final threshold was selected.
    """

    tier = get_rr_resolution(ticker, yf_info=yf_info)
    regime_text = str(regime or "").strip().upper()
    regime_multiplier = (
        REGIME_RR_SCALING.get(regime_text, 1.0) if regime_text else 1.0
    )
    regime_minimum = get_rr_minimum(
        ticker,
        regime=regime_text or None,
        yf_info=yf_info,
    )
    required_rr = round(max(USER_EXECUTION_RR_FLOOR, regime_minimum), 3)
    return RequiredRRResolution(
        ticker=tier.ticker,
        required_rr=required_rr,
        base_rr_minimum=tier.rr_minimum,
        regime_rr_minimum=regime_minimum,
        user_execution_floor=USER_EXECUTION_RR_FLOOR,
        execution_regime=regime_text or "UNSPECIFIED",
        regime_multiplier=regime_multiplier,
        tier_name=tier.tier_name,
        tier_label=tier.tier_label,
        tier_source=tier.source,
        market_cap_idr=tier.market_cap_idr,
    )


def get_required_rr_minimum(
    ticker: str,
    regime: str | None = None,
    yf_info: dict[str, Any] | None = None,
) -> float:
    """Return ``max(2.0, get_rr_minimum(...))`` for execution gates."""

    return get_required_rr_resolution(
        ticker,
        regime=regime,
        yf_info=yf_info,
    ).required_rr


def get_rr_tier_name(ticker: str, yf_info: dict[str, Any] | None = None) -> str:
    """Return the resolved R/R tier name for a ticker."""
    return get_rr_resolution(ticker, yf_info=yf_info).tier_name


def get_rr_tier_label(ticker: str, yf_info: dict[str, Any] | None = None) -> str:
    """Return the human-readable resolved R/R tier label for a ticker."""
    return get_rr_resolution(ticker, yf_info=yf_info).tier_label


def format_rr_resolution_context(resolution: RRTierResolution) -> str:
    """Return a human-readable threshold source suffix for coherence messages."""
    if resolution.source == "market_cap" and resolution.market_cap_idr is not None:
        market_cap_t = resolution.market_cap_idr / 1_000_000_000_000
        return f"({resolution.tier_name} tier - marketCap Rp {market_cap_t:.0f}T)"
    if resolution.source == "static_fallback":
        return f"({resolution.tier_name} tier - static fallback)"
    return f"({resolution.tier_name} tier)"


def _log_rr_resolution(resolution: RRTierResolution) -> None:
    """Emit auditable DEBUG logging for R/R tier resolution."""
    if resolution.source == "market_cap" and resolution.market_cap_idr is not None:
        logger.debug(
            f"[RRTier] {resolution.ticker} -> tier={resolution.tier_name}, "
            f"source=market_cap, marketCap=Rp "
            f"{resolution.market_cap_idr / 1_000_000_000_000:.1f}T, "
            f"rr_minimum={resolution.rr_minimum:.1f}x"
        )
        return
    logger.debug(
        f"[RRTier] {resolution.ticker} -> tier={resolution.tier_name}, "
        f"source={resolution.source}, rr_minimum={resolution.rr_minimum:.1f}x"
    )


# ── Task 8: Trailing Stop Computation ────────────────────────────────────────

_TRAILING_STOP_MULTIPLIER: dict[str, float] = {
    "BULL": 1.5,
    "SIDEWAYS": 1.8,
    "UNKNOWN": 2.5,
    "LOW": 1.5,
    "NORMAL": 1.5,
    "HIGH": 1.8,
    "RECOVERY": 2.0,
    "DEFENSIVE": 2.5,
}
_TRAILING_STOP_MULTIPLIER_DEFAULT: float = 1.5


def compute_exit_plan(
    entry_price: float,
    t1_price: float,
    t2_price: float | None,
    stop_price: float,
    trailing_stop_pct: float | None = None,
) -> dict:
    """Two-tranche partial exit plan for swing trades.

    T1 = first target (50% position exit).
    T2 = second target if available; else trail the remainder.
    trailing_stop_pct is the ATR-based trail distance computed by compute_trailing_stop.
    """
    risk = entry_price - stop_price
    if risk <= 0:
        return {
            "t1_exit_pct": 0.50,
            "t1_gain_pct": None,
            "t2_exit_pct": 0.50 if t2_price else None,
            "t2_gain_pct": None,
            "trail_remainder": True,
            "trail_trigger_price": None,
            "exit_note": "Invalid stop — stop must be below entry",
        }

    t1_gain = (t1_price - entry_price) / entry_price
    t2_gain = (t2_price - entry_price) / entry_price if t2_price else None

    trail_trigger = None
    if trailing_stop_pct and trailing_stop_pct > 0:
        trail_trigger = round(entry_price * (1 + trailing_stop_pct * 0.5), 0)

    notes = []
    notes.append(f"Exit 50% at T1 ({t1_price:.0f}, +{t1_gain:.1%})")
    if t2_price:
        notes.append(f"Exit remaining 50% at T2 ({t2_price:.0f}, +{t2_gain:.1%})")
    else:
        notes.append("Trail remainder with ATR stop after T1 is hit")

    return {
        "t1_exit_pct": 0.50,
        "t1_gain_pct": round(t1_gain * 100, 2),
        "t2_exit_pct": 0.50 if t2_price else None,
        "t2_gain_pct": round(t2_gain * 100, 2) if t2_gain is not None else None,
        "trail_remainder": t2_price is None,
        "trail_trigger_price": trail_trigger,
        "exit_note": " | ".join(notes),
    }


def compute_trailing_stop(
    entry_price: float,
    atr_14: float,
    regime: str = "NORMAL",
) -> dict:
    """ATR-based trailing stop with regime-aware multiplier.

    Activation fires once the position moves activation_pct in the trader's
    favour.  Trail distance widens in RECOVERY/DEFENSIVE to accommodate higher
    volatility without being stopped out prematurely.
    """
    multiplier = _TRAILING_STOP_MULTIPLIER.get(
        str(regime).upper(), _TRAILING_STOP_MULTIPLIER_DEFAULT
    )
    trail_distance = atr_14 * multiplier
    trail_pct = trail_distance / entry_price
    activation_pct = max(0.03, trail_pct * 0.5)
    return {
        "trailing_stop_pct": round(trail_pct, 4),
        "trailing_stop_trigger_pct": round(activation_pct, 4),
        "trailing_stop_distance_rp": round(trail_distance, 2),
    }
