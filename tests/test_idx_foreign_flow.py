"""Tests for providers/idx_foreign_flow.py — uses the real ADRO/BBCA fixture shapes."""

from unittest.mock import MagicMock

import pytest

from providers.idx_foreign_flow import (
    ForeignFlowSnapshot,
    _empty,
    _safe_pct,
    _safe_raw,
    fetch_foreign_flow,
)

# ---------------------------------------------------------------------------
# Fixture: mirrors the real Stockbit findata-view response for ADRO 2026-06-18
# ---------------------------------------------------------------------------
_ADRO_RESPONSE = {
    "message": "Successfully get chart data",
    "data": {
        "summary": {
            "date_range": "18 Jun 2026",
            "foreign_buy": {"label": "F Buy", "value": {"raw": 23647823000, "formatted": "23.65 B"}},
            "foreign_sell": {"label": "F Sell", "value": {"raw": 37462896000, "formatted": "37.46 B"}},
            "net_foreign": {"label": "Net Foreign Sell (Regular)", "value": {"raw": -13815073000, "formatted": "-13.82 B"}},
            "domestic_buy": {"label": "D Buy", "value": {"raw": 27056508000, "formatted": "27.06 B"}},
            "domestic_sell": {"label": "D Sell", "value": {"raw": 13241435000, "formatted": "13.24 B"}},
            "net_domestic": {"label": "Net Domestic Buy (Regular)", "value": {"raw": 13815073000, "formatted": "13.82 B"}},
            "volume": {
                "foreign_buy": {"label": "F Buy", "value": {"raw": 10391100, "formatted": "10.39 M"}},
                "foreign_sell": {"label": "F Sell", "value": {"raw": 16450200, "formatted": "16.45 M"}},
                "net_foreign_reguler": {"label": "Net Foreign Sell (Regular)", "value": {"raw": -6059100, "formatted": "-6.06 M"}},
                "net_foreign_all_market": {"label": "Net Foreign Sell (All Market)", "value": {"raw": -6059100, "formatted": "-6.06 M"}},
                "domestic_buy": {"label": "D Buy", "value": {"raw": 11857900, "formatted": "11.86 M"}},
                "domestic_sell": {"label": "D Sell", "value": {"raw": 5798800, "formatted": "5.80 M"}},
            },
        },
        "value": {
            "label": "Value (IDR)",
            "total": {"raw": 50704331000, "formatted": "50.70 B"},
            "foreign_total": {"value": {"raw": 61110719000}, "percentage": {"raw": 60.261833, "formatted": "60.26%"}},
            "domestic_total": {"value": {"raw": 40297943000}, "percentage": {"raw": 39.738167}},
        },
        "volume": {
            "label": "Volume (Shares)",
            "total": {"raw": 22249000, "formatted": "22.25 M"},
            "foreign_total": {"value": {"raw": 26841300}, "percentage": {"raw": 60.32024, "formatted": "60.32%"}},
            "domestic_total": {"value": {"raw": 17656700}, "percentage": {"raw": 39.67976}},
        },
        "last_updated": "18 Jun 2026",
        "from": "2026-06-18",
        "to": "2026-06-18",
    },
}

_NET_BUY_RESPONSE = {
    "message": "Successfully get chart data",
    "data": {
        "summary": {
            "foreign_buy": {"label": "F Buy", "value": {"raw": 5000000000}},
            "foreign_sell": {"label": "F Sell", "value": {"raw": 2000000000}},
            "net_foreign": {"label": "Net Foreign Buy (Regular)", "value": {"raw": 3000000000}},
            "volume": {
                "net_foreign_reguler": {"label": "Net Foreign Buy (Regular)", "value": {"raw": 1500000}},
            },
        },
        "volume": {
            "foreign_total": {"value": {"raw": 4000000}, "percentage": {"raw": 45.5}},
        },
        "from": "2026-06-18",
    },
}


# ---------------------------------------------------------------------------
# Unit tests — _safe_raw and _safe_pct helpers
# ---------------------------------------------------------------------------

def test_safe_raw_extracts_value():
    mapping = {"net_foreign": {"label": "X", "value": {"raw": -13815073000, "formatted": "-13.82 B"}}}
    assert _safe_raw(mapping, "net_foreign") == -13815073000.0


def test_safe_raw_missing_key_returns_none():
    assert _safe_raw({}, "missing") is None


def test_safe_raw_missing_value_raw_returns_none():
    mapping = {"x": {"label": "X", "value": {}}}
    assert _safe_raw(mapping, "x") is None


def test_safe_pct_extracts_percentage():
    mapping = {"foreign_total": {"value": {"raw": 26841300}, "percentage": {"raw": 60.32024}}}
    assert _safe_pct(mapping, "foreign_total") == pytest.approx(60.32024)


def test_safe_pct_missing_returns_none():
    assert _safe_pct({}, "foreign_total") is None


# ---------------------------------------------------------------------------
# Unit tests — fetch_foreign_flow with mock client
# ---------------------------------------------------------------------------

def _mock_client(response: dict) -> MagicMock:
    client = MagicMock()
    client.get.return_value = response
    return client


def test_fetch_foreign_flow_net_sell_snapshot():
    client = _mock_client(_ADRO_RESPONSE)
    snap = fetch_foreign_flow("ADRO", client)

    assert snap.ticker == "ADRO"
    assert snap.net_foreign_flow_m == pytest.approx(-13815.07, abs=0.01)
    assert snap.foreign_buy_m == pytest.approx(23647.82, abs=0.01)
    assert snap.foreign_sell_m == pytest.approx(37462.90, abs=0.01)
    assert snap.foreign_vol_pct == pytest.approx(60.32024)
    assert snap.net_foreign_vol == -6059100
    assert snap.is_net_foreign_buy is False
    assert snap.as_of_date == "2026-06-18"
    assert snap.source == "stockbit_foreign_flow"


def test_fetch_foreign_flow_net_buy_snapshot():
    client = _mock_client(_NET_BUY_RESPONSE)
    snap = fetch_foreign_flow("BBRI", client)

    assert snap.net_foreign_flow_m == pytest.approx(3000.0)
    assert snap.is_net_foreign_buy is True
    assert snap.net_foreign_vol == 1500000
    assert snap.foreign_vol_pct == pytest.approx(45.5)


def test_fetch_foreign_flow_empty_response_returns_empty():
    client = _mock_client({})
    snap = fetch_foreign_flow("TLKM", client)

    assert snap.ticker == "TLKM"
    assert snap.net_foreign_flow_m is None
    assert snap.is_net_foreign_buy is None


def test_fetch_foreign_flow_exception_returns_empty():
    client = MagicMock()
    client.get.side_effect = Exception("401 Unauthorized after retrying authentication.")
    snap = fetch_foreign_flow("BBCA", client)

    assert snap.net_foreign_flow_m is None
    assert snap.is_net_foreign_buy is None
    assert snap.source == "stockbit_foreign_flow"


def test_fetch_foreign_flow_missing_summary_returns_empty():
    client = _mock_client({"message": "ok", "data": {"from": "2026-06-18"}})
    snap = fetch_foreign_flow("ASII", client)

    assert snap.net_foreign_flow_m is None
    assert snap.as_of_date == "2026-06-18"  # date is read even when summary is absent


# ---------------------------------------------------------------------------
# Unit test — _empty helper
# ---------------------------------------------------------------------------

def test_empty_snapshot_has_correct_ticker():
    snap = _empty("UNTR")
    assert snap.ticker == "UNTR"
    assert snap.net_foreign_flow_m is None
    assert snap.is_net_foreign_buy is None
    assert snap.source == "stockbit_foreign_flow"
