"""
Per-ticker broker flow summary from Stockbit broker distribution endpoint.

Importers: services/debate_chamber.py (_synthesizer_node)
Endpoint:  GET https://exodus.stockbit.com/order-trade/broker/distribution
Data:      top-5 buyer/seller broker codes, net accumulation signal
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from urllib.parse import urlencode

from utils.ticker import InvalidIDXTicker, normalize_idx_ticker

logger = logging.getLogger(__name__)

_BASE_URL = "https://exodus.stockbit.com"
_BROKER_DISTRIBUTION_PATH = "/order-trade/broker/distribution"
_INVESTOR_TYPE = "INVESTOR_TYPE_ALL"
_MARKET_BOARD = "MARKET_TYPE_REGULER"
_DATA_TYPE = "BROKER_DISTRIBUTION_DATA_TYPE_VALUE"
_PERIOD = "TB_PERIOD_LAST_1_DAY"


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
    if ticker is None or not str(ticker).strip():
        return ""
    return normalize_idx_ticker(ticker)


def _safe_float(val) -> float | None:
    if isinstance(val, dict):
        for key in (
            "raw",
            "value",
            "amount",
            "total",
            "total_value",
            "net",
            "lot",
            "volume",
        ):
            if key in val:
                parsed = _safe_float(val.get(key))
                if parsed is not None:
                    return parsed
        return None
    try:
        if isinstance(val, bool):
            return None  # bool is int subclass; float(True)==1.0 would silently fabricate volume
        if isinstance(val, str):
            val = (
                val.replace("Rp", "")
                .replace(",", "")
                .replace(" ", "")
                .strip()
            )
        v = float(val)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def _first_present(mapping: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return None


def _broker_code(entry: dict) -> str:
    detail_node = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
    broker_node = entry.get("broker") if isinstance(entry.get("broker"), dict) else {}
    buyer_node = entry.get("buyer") if isinstance(entry.get("buyer"), dict) else {}
    seller_node = entry.get("seller") if isinstance(entry.get("seller"), dict) else {}
    code = (
        detail_node.get("code")
        or detail_node.get("broker_code")
        or broker_node.get("code")
        or buyer_node.get("code")
        or seller_node.get("code")
        or _first_present(
            entry,
            (
                "code",
                "broker_code",
                "buyer_code",
                "seller_code",
                "brokerCode",
                "brokerCodeBuyer",
                "brokerCodeSeller",
            ),
        )
        or ""
    )
    return str(code)


def _broker_name(entry: dict) -> str:
    detail_node = entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
    broker_node = entry.get("broker") if isinstance(entry.get("broker"), dict) else {}
    buyer_node = entry.get("buyer") if isinstance(entry.get("buyer"), dict) else {}
    seller_node = entry.get("seller") if isinstance(entry.get("seller"), dict) else {}
    name = (
        detail_node.get("name")
        or detail_node.get("broker_name")
        or broker_node.get("name")
        or buyer_node.get("name")
        or seller_node.get("name")
        or _first_present(
            entry,
            (
                "name",
                "broker_name",
                "buyer_name",
                "seller_name",
                "company",
                "brokerName",
            ),
        )
        or ""
    )
    return str(name)


def _extract_broker_list(entries: list, top_n: int = 5) -> list[dict]:
    """
    Normalize a Stockbit broker list into [{code, name, lot, value_m}].
    Handles two known Stockbit shapes:
      Shape A: {"broker": {"code": "YP", "name": "..."}, "lot": N, "value": N}
      Shape B: {"code": "YP", "name": "...", "lot": N, "value": N}
      Shape C: order-trade broker distribution top_broker_buy/top_broker_sell rows
    """
    result = []
    for entry in entries[:top_n]:
        if not isinstance(entry, dict):
            continue
        detail_node = (
            entry.get("detail") if isinstance(entry.get("detail"), dict) else {}
        )
        code = _broker_code(entry)
        name = _broker_name(entry)
        _detail_lot = _first_present(
            detail_node, ("lot", "lots", "volume", "total_lot", "total_volume")
        )
        _entry_lot = _first_present(
            entry,
            ("lot", "lots", "volume", "lot_volume", "total_lot", "total_volume",
             "buy_lot", "sell_lot"),
        )
        lot = _safe_float(_detail_lot if _detail_lot is not None else _entry_lot)
        _detail_value = _first_present(
            detail_node, ("amount", "value", "total_value", "totalValue")
        )
        _entry_value = _first_present(
            entry,
            ("amount", "value", "total_value", "value_idr", "totalValue",
             "transaction_value", "buy_value", "sell_value", "net_value"),
        )
        raw_value = _safe_float(_detail_value if _detail_value is not None else _entry_value)
        value_m = round(raw_value / 1_000_000, 2) if raw_value is not None else None
        if code:
            result.append({"code": str(code), "name": str(name), "lot": lot, "value_m": value_m})
    return result


def _codes_string(broker_list: list[dict]) -> str | None:
    codes = [b["code"] for b in broker_list if b.get("code")]
    return ", ".join(codes) if codes else None


def _broker_distribution_url(ticker: str) -> str:
    query = urlencode(
        {
            "date": "",  # empty = latest trading day (API default)
            "symbol": ticker,
            "investor_type": _INVESTOR_TYPE,
            "market_board": _MARKET_BOARD,
            "data_type": _DATA_TYPE,
            "period": _PERIOD,
        }
    )
    return f"{_BASE_URL}{_BROKER_DISTRIBUTION_PATH}?{query}"


def _broker_distribution_section(data: dict) -> dict:
    by_value = data.get("by_value")
    if isinstance(by_value, dict):
        return by_value
    by_lot = data.get("by_lot")
    if isinstance(by_lot, dict):
        return by_lot
    return data


def fetch_broker_summary(ticker: str, client=None) -> BrokerSummarySnapshot:
    """
    Fetch top-5 buyer/seller broker codes for one ticker via Stockbit.

    Returns a BrokerSummarySnapshot with empty lists on any failure so callers
    can always proceed without a guard.
    """
    try:
        ticker = _normalize_ticker(ticker)
    except InvalidIDXTicker as exc:
        logger.warning("[BrokerSummary] invalid ticker rejected before fetch: %s", exc)
        return _empty("")
    if not ticker:
        return _empty(ticker)

    if client is None:
        try:
            from services.stockbit_api_client import StockbitApiClient
            client = StockbitApiClient()
        except Exception as exc:
            logger.warning("[BrokerSummary] client init failed for %s: %s", ticker, exc)
            return _empty(ticker)

    url = _broker_distribution_url(ticker)

    try:
        resp = client.get(url, operation="broker summary", optional=True)
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
    broker_data = _broker_distribution_section(data)

    # Try multiple key names for buy/sell lists (defensive against Stockbit schema changes)
    buy_list = (
        broker_data.get("top_broker_buy")
        or broker_data.get("buy")
        or broker_data.get("broker_buy")
        or broker_data.get("top_buy")
        or []
    )
    sell_list = (
        broker_data.get("top_broker_sell")
        or broker_data.get("sell")
        or broker_data.get("broker_sell")
        or broker_data.get("top_sell")
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
