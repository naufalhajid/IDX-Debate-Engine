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


def test_cluster_cap_none_preserves_old_behavior() -> None:
    """max_per_cluster=None (default) tidak mengubah perilaku sebelum V4.3."""
    scorable = [
        _make_entry("ADRO", 0.90, "energy"),
        _make_entry("PTBA", 0.85, "basic_materials"),  # cluster sama dengan energy
        _make_entry("KLBF", 0.70, "healthcare"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=1, min_conviction=0.0
    )
    # Sektor beda (energy vs basic_materials) jadi sector cap sendiri tidak menahan;
    # tanpa max_per_cluster, cluster co-move keduanya diabaikan sepenuhnya.
    assert len(result) == 3


def test_cluster_cap_enforced_across_different_sectors() -> None:
    """energy + basic_materials satu cluster commodity_cyclical; cap membatasi total.

    4 candidates (bukan 3) supaya top_n=3 tercapai lewat ADRO+KLBF+ICBP tanpa
    butuh PTBA — soft-cap fallback (by design, sama seperti sector cap) tidak
    terpicu, sehingga cluster cap benar-benar teruji, bukan diselamatkan fallback.
    """
    scorable = [
        _make_entry("ADRO", 0.90, "energy"),
        _make_entry("PTBA", 0.85, "basic_materials"),
        _make_entry("KLBF", 0.75, "healthcare"),
        _make_entry("ICBP", 0.70, "consumer_staples"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=2, min_conviction=0.0, max_per_cluster=1
    )
    tickers = [e["ticker"] for e in result]
    # ADRO (skor tertinggi) masuk duluan dan mengisi slot commodity_cyclical;
    # PTBA ditolak cluster cap meski sektornya beda dan sector cap longgar (2).
    assert "ADRO" in tickers
    assert "PTBA" not in tickers
    assert "KLBF" in tickers
    assert "ICBP" in tickers


def test_cluster_cap_soft_fallback_fills_top_n() -> None:
    """Jika semua candidates satu cluster, soft-cap fallback tetap mengisi top_n."""
    scorable = [
        _make_entry("ADRO", 0.90, "energy"),
        _make_entry("PTBA", 0.85, "basic_materials"),
        _make_entry("MEDC", 0.80, "energy"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=2, min_conviction=0.0, max_per_cluster=1
    )
    # Sama seperti soft-cap sektor: alpha dijaga, top_n tetap terisi walau
    # cluster cap terlampaui.
    assert len(result) == 3


def test_cluster_cap_ignores_unmapped_sectors() -> None:
    """Sektor di luar SECTOR_CORRELATION_CLUSTERS tidak pernah kena cluster cap."""
    scorable = [
        _make_entry("TLKM", 0.90, "tech"),
        _make_entry("KLBF", 0.85, "healthcare"),
        _make_entry("ICBP", 0.80, "consumer_staples"),
    ]
    result = diversify_portfolio(
        scorable, top_n=3, max_per_sector=1, min_conviction=0.0, max_per_cluster=1
    )
    # Ketiga sektor tidak dipetakan ke cluster manapun -> cluster cap tidak
    # berlaku sama sekali, hanya sector cap (yang sudah longgar karena beda sektor).
    assert len(result) == 3


def test_cluster_cap_custom_mapping_override() -> None:
    """sector_clusters override memungkinkan mapping kustom tanpa menyentuh default modul.

    3 candidates (bukan 2) supaya top_n=2 tercapai lewat KLBF+BBCA tanpa butuh
    TLKM — soft-cap fallback tidak terpicu, cluster cap kustom benar-benar teruji.
    """
    scorable = [
        _make_entry("KLBF", 0.90, "healthcare"),
        _make_entry("TLKM", 0.85, "tech"),
        _make_entry("BBCA", 0.80, "bank"),
    ]
    result = diversify_portfolio(
        scorable,
        top_n=2,
        max_per_sector=1,
        min_conviction=0.0,
        max_per_cluster=1,
        sector_clusters={"healthcare": "custom_cluster", "tech": "custom_cluster"},
    )
    tickers = [e["ticker"] for e in result]
    assert "KLBF" in tickers
    assert "TLKM" not in tickers
    assert "BBCA" in tickers
