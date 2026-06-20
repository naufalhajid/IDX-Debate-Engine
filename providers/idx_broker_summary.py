"""
Per-ticker broker flow summary from Stockbit broker-summary endpoint.

Importers: services/debate_chamber.py (_synthesizer_node)
Endpoint:  GET https://exodus.stockbit.com/broker-summary/v1/{ticker}
Data:      top-5 buyer/seller broker codes, net accumulation signal
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_BASE_URL = "https://exodus.stockbit.com"


@dataclass
class BrokerSummarySnapshot:
    ticker: str
    top_buyers: list[dict] = field(default_factory=list)
    top_sellers: list[dict] = field(default_factory=list)
    top_buyer_codes: str | None = None    # "YP, MG, BQ" — prompt-ready
    top_seller_codes: str | None = None   # "AK, CS, DX"
    net_broker_buy_m: float | None = None # net buy in millions IDR (positive = accumulation)
    is_accumulation: bool | None = None   # True if top brokers net buy
    source: str = "stockbit_broker_summary"


def _empty(ticker: str) -> BrokerSummarySnapshot:
    return BrokerSummarySnapshot(ticker=ticker)


def _normalize_ticker(ticker: str) -> str:
    t = str(ticker or "").strip().upper()
    return t[:-3] if t.endswith(".JK") else t


def _safe_float(val) -> float | None:
    try:
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _extract_broker_list(entries: list, top_n: int = 5) -> list[dict]:
    """
    Normalize a Stockbit broker list into [{code, name, lot, value_m}].
    Handles two known Stockbit shapes:
      Shape A: {"broker": {"code": "YP", "name": "..."}, "lot": N, "value": N}
      Shape B: {"code": "YP", "name": "...", "lot": N, "value": N}
    """
    result = []
    for entry in entries[:top_n]:
        if not isinstance(entry, dict):
            continue
        broker_node = entry.get("broker") or {}
        code = (
            broker_node.get("code")
            or entry.get("code")
            or entry.get("broker_code")
            or ""
        )
        name = (
            broker_node.get("name")
            or entry.get("name")
            or entry.get("broker_name")
            or ""
        )
        lot = _safe_float(next((entry.get(k) for k in ("lot", "volume", "lot_volume") if entry.get(k) is not None), None))
        raw_value = _safe_float(next((entry.get(k) for k in ("value", "value_idr", "total_value") if entry.get(k) is not None), None))
        value_m = round(raw_value / 1_000_000, 2) if raw_value is not None else None
        if code:
            result.append({"code": str(code), "name": str(name), "lot": lot, "value_m": value_m})
    return result


def _codes_string(broker_list: list[dict]) -> str | None:
    codes = [b["code"] for b in broker_list if b.get("code")]
    return ", ".join(codes) if codes else None


def fetch_broker_summary(ticker: str, client=None) -> BrokerSummarySnapshot:
    """
    Fetch top-5 buyer/seller broker codes for one ticker via Stockbit.

    Returns a BrokerSummarySnapshot with empty lists on any failure so callers
    can always proceed without a guard.
    """
    ticker = _normalize_ticker(ticker)
    if not ticker:
        return _empty(ticker)

    if client is None:
        try:
            from services.stockbit_api_client import StockbitApiClient
            client = StockbitApiClient()
        except Exception as exc:
            logger.warning("[BrokerSummary] client init failed for %s: %s", ticker, exc)
            return _empty(ticker)

    url = f"{_BASE_URL}/broker-summary/v1/{ticker}"

    try:
        resp = client.get(url)
    except Exception as exc:
        logger.warning("[BrokerSummary] fetch failed for %s: %s", ticker, exc)
        return _empty(ticker)

    if not isinstance(resp, dict):
        logger.warning("[BrokerSummary] non-dict response for %s", ticker)
        return _empty(ticker)

    data = resp.get("data") or resp
    if not isinstance(data, dict):
        logger.warning("[BrokerSummary] unexpected data shape for %s: %s", ticker, type(data))
        return _empty(ticker)

    logger.debug("[BrokerSummary] %s data keys: %s", ticker, list(data.keys()))

    # Try multiple key names for buy/sell lists (defensive against Stockbit schema changes)
    buy_list = (
        data.get("buy")
        or data.get("broker_buy")
        or data.get("top_buy")
        or []
    )
    sell_list = (
        data.get("sell")
        or data.get("broker_sell")
        or data.get("top_sell")
        or []
    )

    top_buyers = _extract_broker_list(buy_list)
    top_sellers = _extract_broker_list(sell_list)

    # Net broker flow: sum of top buyer values minus sum of top seller values.
    # Only set net_m when at least one side has real IDR value data; avoids
    # false is_accumulation=False when API returns broker codes but no values.
    buy_total = sum(b["value_m"] for b in top_buyers if b.get("value_m") is not None)
    sell_total = sum(s["value_m"] for s in top_sellers if s.get("value_m") is not None)
    net_m: float | None = None
    if any(b.get("value_m") is not None for b in top_buyers + top_sellers):
        net_m = round(buy_total - sell_total, 2)

    logger.info(
        "[BrokerSummary] %s — buyers: %s | sellers: %s | net: %s M",
        ticker,
        _codes_string(top_buyers),
        _codes_string(top_sellers),
        net_m,
    )

    return BrokerSummarySnapshot(
        ticker=ticker,
        top_buyers=top_buyers,
        top_sellers=top_sellers,
        top_buyer_codes=_codes_string(top_buyers),
        top_seller_codes=_codes_string(top_sellers),
        net_broker_buy_m=net_m,
        is_accumulation=(net_m > 0) if net_m is not None else None,
    )
