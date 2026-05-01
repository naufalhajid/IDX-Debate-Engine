"""
core/portfolio_optimizer.py — Risk diversification untuk Top N selection.

Algoritma greedy sector-cap:
  1. Filter candidates berdasarkan min_conviction threshold.
  2. Iterasi dari skor tertinggi; accept jika sektor belum mencapai max_per_sector.
  3. Soft-cap fallback: jika setelah greedy pass jumlah < top_n, isi dari overflow
     (kandidat yang ditolak karena sektor cap) untuk menghindari top N yang terlalu kecil.
  4. Tie-breaking di boundary cutoff: candidates dengan skor = cutoff terakhir
     ikut disertakan semua (konsisten dengan perilaku select_top_n sebelumnya).
"""

from __future__ import annotations

from utils.logger_config import logger


def diversify_portfolio(
    scorable: list[dict],
    top_n: int,
    max_per_sector: int,
    min_conviction: float,
) -> list[dict]:
    """
    Pilih top_n saham dengan sector diversification.

    Args:
        scorable: List entry sudah di-sort descending by conviction_score,
                  masing-masing harus punya field "sector_key" dan "conviction_score".
        top_n: Jumlah saham yang ingin dipilih.
        max_per_sector: Batas jumlah saham per sektor.
        min_conviction: Minimum conviction score agar eligible.

    Returns:
        List entry terpilih, panjang <= top_n (bisa lebih karena tie-breaking).
    """
    # Step 1: Filter by min conviction
    eligible = [e for e in scorable if e.get("conviction_score", 0.0) >= min_conviction]

    if not eligible:
        logger.warning(
            f"[Portfolio] Tidak ada kandidat dengan conviction >= {min_conviction:.0%}. "
            "Fallback ke semua scorable tanpa conviction filter."
        )
        eligible = list(scorable)

    # Step 2: Greedy sector cap — iterate sorted-by-score, accept jika slot tersedia
    sector_counts: dict[str, int] = {}
    selected: list[dict] = []
    overflow: list[dict] = []  # ditolak karena sektor cap; kandidat fallback

    for entry in eligible:
        sector = entry.get("sector_key", "unknown")
        count = sector_counts.get(sector, 0)

        if count < max_per_sector:
            selected.append(entry)
            sector_counts[sector] = count + 1
        else:
            overflow.append(entry)

        if len(selected) >= top_n:
            break

    # Step 3: Soft-cap fallback — jika selected < top_n karena sector cap terlalu ketat
    if len(selected) < top_n and overflow:
        deficit = top_n - len(selected)
        fallback_entries = overflow[:deficit]
        logger.warning(
            f"[Portfolio] Soft-cap fallback: menambahkan {len(fallback_entries)} kandidat "
            f"(total sector counts akan melebihi max_per_sector={max_per_sector}). "
            "Alpha dijaga agar top N tidak diisi saham berkualitas lebih rendah."
        )
        selected.extend(fallback_entries)

    # Step 4: Tie-breaking di boundary — sertakan semua yang tie dengan entry terakhir
    if len(selected) >= top_n:
        cutoff_score = selected[top_n - 1].get("conviction_score", 0.0)
        selected_set = {id(e) for e in selected}

        for entry in eligible:
            if id(entry) not in selected_set:
                if entry.get("conviction_score", 0.0) == cutoff_score:
                    selected.append(entry)
                    logger.info(
                        f"[Portfolio] Tie included: {entry.get('ticker', '?')} "
                        f"(score={cutoff_score:.4f})"
                    )

    logger.info(
        f"[Portfolio] {len(selected)} dipilih dari {len(eligible)} eligible "
        f"(top_n={top_n}, sector_cap={max_per_sector}, "
        f"min_conviction={min_conviction:.0%})"
    )
    return selected
