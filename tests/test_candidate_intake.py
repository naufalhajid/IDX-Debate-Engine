from core.candidate_intake import normalize, normalize_batch


def test_normalize_standard_fields() -> None:
    result = normalize(
        {
            "ticker": "BBCA",
            "price": 9000,
            "market_cap": "1100000000000",
            "sector": "bank",
            "source": "quant_filter",
        }
    )

    assert result.ticker == "BBCA"
    assert result.price == 9000.0
    assert result.market_cap == 1_100_000_000_000.0
    assert result.sector == "bank"
    assert result.source == "quant_filter"


def test_normalize_remaps_symbol_to_ticker() -> None:
    result = normalize(
        {
            "symbol": "ADRO",
            "last_price": "2420",
            "source": "manual_screen",
        }
    )

    assert result.ticker == "ADRO"
    assert result.price == 2420.0
    assert result.market_cap is None
    assert result.sector is None
    assert result.source == "manual_screen"


def test_normalize_batch_rejects_missing_price() -> None:
    valid, rejected = normalize_batch(
        [
            {"ticker": "BBRI", "close": 4100, "source": "quant_filter"},
            {"ticker": "TLKM", "source": "quant_filter"},
        ]
    )

    assert [candidate.ticker for candidate in valid] == ["BBRI"]
    assert len(rejected) == 1
    assert rejected[0]["candidate"] == {"ticker": "TLKM", "source": "quant_filter"}
    assert "missing required price" in rejected[0]["error"]


def test_normalize_batch_empty_input() -> None:
    valid, rejected = normalize_batch([])

    assert valid == []
    assert rejected == []
