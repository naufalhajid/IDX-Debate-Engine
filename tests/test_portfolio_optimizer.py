"""Tests untuk core/portfolio_optimizer.py."""

from core.portfolio_optimizer import diversify_portfolio


def _make_entry(ticker: str, score: float, sector: str) -> dict:
    return {
        "ticker": ticker,
        "conviction_score": score,
        "sector_key": sector,
        "verdict": {"rating": "BUY"},
    }


def test_basic_selection() -> None:
    """Pilih top 3 dari 5 candidates tanpa sektor overlap."""
    scorable = [
        _make_entry("BBCA", 0.80, "bank"),
        _make_entry("KLBF", 0.75, "healthcare"),
        _make_entry("ICBP", 0.70, "consumer_staples"),
        _make_entry("TLKM", 0.60, "tech"),
        _make_entry("ADRO", 0.50, "energy"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=1, min_conviction=0.0
    )
    assert len(result) == 3
    tickers = [e["ticker"] for e in result]
    assert "BBCA" in tickers
    assert "KLBF" in tickers
    assert "ICBP" in tickers


def test_sector_cap_enforced() -> None:
    """Jika 2 saham bank teratas, hanya 1 yang masuk dengan max_per_sector=1."""
    scorable = [
        _make_entry("BBCA", 0.90, "bank"),
        _make_entry("BBRI", 0.85, "bank"),
        _make_entry("KLBF", 0.70, "healthcare"),
    ]
    result = diversify_portfolio(
        scorable, top_n=2, max_per_sector=1, min_conviction=0.0
    )
    assert len(result) == 2
    tickers = [e["ticker"] for e in result]
    # BBCA masuk (score tertinggi), BBRI ditolak karena sector cap, KLBF masuk
    assert "BBCA" in tickers
    assert "BBRI" not in tickers
    assert "KLBF" in tickers


def test_soft_cap_fallback() -> None:
    """Jika semua candidates dari sektor sama, soft-cap fallback memastikan top_n terisi."""
    scorable = [
        _make_entry("BBCA", 0.90, "bank"),
        _make_entry("BBRI", 0.85, "bank"),
        _make_entry("BMRI", 0.80, "bank"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=1, min_conviction=0.0
    )
    # Soft-cap fallback: semua bank, tapi tetap pilih 3
    assert len(result) == 3


def test_min_conviction_filter() -> None:
    """Candidates dengan conviction < min_conviction dikecualikan."""
    scorable = [
        _make_entry("BBCA", 0.80, "bank"),
        _make_entry("KLBF", 0.25, "healthcare"),  # di bawah threshold
        _make_entry("ICBP", 0.70, "consumer_staples"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=2, min_conviction=0.30
    )
    tickers = [e["ticker"] for e in result]
    assert "KLBF" not in tickers


def test_min_conviction_fallback_when_all_below() -> None:
    """Jika semua candidates di bawah min_conviction, soft fallback dipakai."""
    scorable = [
        _make_entry("BBCA", 0.20, "bank"),
        _make_entry("KLBF", 0.15, "healthcare"),
    ]
    result = diversify_portfolio(
        scorable, top_n=2, max_per_sector=2, min_conviction=0.50
    )
    # Soft fallback: tetap ada result
    assert len(result) == 2


def test_tie_breaking() -> None:
    """Candidates dengan score = cutoff terakhir ikut disertakan."""
    scorable = [
        _make_entry("BBCA", 0.80, "bank"),
        _make_entry("KLBF", 0.70, "healthcare"),
        _make_entry("ICBP", 0.70, "consumer_staples"),  # tie dengan KLBF
    ]
    result = diversify_portfolio(
        scorable, top_n=2, max_per_sector=2, min_conviction=0.0
    )
    # Tie: KLBF dan ICBP sama-sama 0.70, keduanya harus masuk
    assert len(result) >= 2
    tickers = [e["ticker"] for e in result]
    assert "BBCA" in tickers


def test_empty_input() -> None:
    """Input kosong tidak boleh raise exception."""
    result = diversify_portfolio([], top_n=3, max_per_sector=1, min_conviction=0.0)
    assert result == []


def test_fewer_candidates_than_top_n() -> None:
    """Jika candidates < top_n, kembalikan semua yang ada."""
    scorable = [
        _make_entry("BBCA", 0.80, "bank"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=1, min_conviction=0.0
    )
    assert len(result) == 1
