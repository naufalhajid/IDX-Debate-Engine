"""
core/portfolio_optimizer.py — Risk diversification untuk Top N selection.

Algoritma greedy sector-cap (+ correlation-cluster cap, V4.3):
  1. Filter candidates berdasarkan min_conviction threshold.
  2. Iterasi dari skor tertinggi; accept jika sektor DAN correlation cluster (jika
     dikonfigurasi) belum mencapai batasnya masing-masing.
  3. Soft-cap fallback: jika setelah greedy pass jumlah < top_n, isi dari overflow
     (kandidat yang ditolak karena sektor/cluster cap) untuk menghindari top N yang
     terlalu kecil.
  4. Tie-breaking di boundary cutoff: candidates dengan skor = cutoff terakhir
     ikut disertakan semua (konsisten dengan perilaku select_top_n sebelumnya).
"""

from __future__ import annotations

from utils.logger_config import logger

# V4.3: correlation-cluster proxy. Sektor IDX yang secara historis cenderung
# co-move lebih kuat daripada implied oleh cap-per-sektor saja, dikelompokkan ke
# cluster yang sama. INI HEURISTIK TERDOKUMENTASI, bukan korelasi return harga
# yang diukur secara statistik — hanya dua cluster yang benar-benar bisa
# dipertanggungjawabkan tanpa data historis:
#   - commodity_cyclical: energy + basic_materials, sama-sama digerakkan siklus
#     harga komoditas global (batubara, logam, minyak/gas).
#   - rate_sensitive: bank + finance_nonbank + property, sama-sama sensitif ke
#     siklus suku bunga BI dan pertumbuhan kredit.
# Sektor lain (industrials, consumer_staples, consumer_disc, healthcare, tech,
# infrastructure, transport, default) TIDAK dipetakan — masing-masing tetap
# hanya dibatasi oleh cap sektor biasa, karena co-movement lintas-sektornya
# tidak cukup jelas untuk diklaim tanpa korelasi return yang benar-benar dihitung.
SECTOR_CORRELATION_CLUSTERS: dict[str, str] = {
    "energy": "commodity_cyclical",
    "basic_materials": "commodity_cyclical",
    "bank": "rate_sensitive",
    "finance_nonbank": "rate_sensitive",
    "property": "rate_sensitive",
}


def diversify_portfolio(
    scorable: list[dict],
    top_n: int,
    max_per_sector: int,
    min_conviction: float,
    max_per_cluster: int | None = None,
    sector_clusters: dict[str, str] | None = None,
) -> list[dict]:
    """
    Pilih top_n saham dengan sector diversification (+ correlation-cluster cap opsional).

    Args:
        scorable: List entry sudah di-sort descending by conviction_score,
                  masing-masing harus punya field "sector_key" dan "conviction_score".
        top_n: Jumlah saham yang ingin dipilih.
        max_per_sector: Batas jumlah saham per sektor.
        min_conviction: Minimum conviction score agar eligible.
        max_per_cluster: Batas jumlah saham per correlation cluster (lihat
            SECTOR_CORRELATION_CLUSTERS). None (default) = cluster cap dimatikan,
            perilaku identik dengan sebelum V4.3 — backward compatible.
        sector_clusters: Override mapping sector_key -> cluster name (default:
            SECTOR_CORRELATION_CLUSTERS module-level). Sektor yang tidak ada di
            mapping tidak pernah kena cluster cap.

    Returns:
        List entry terpilih, panjang <= top_n (bisa lebih karena tie-breaking).
    """
    clusters = (
        sector_clusters if sector_clusters is not None else SECTOR_CORRELATION_CLUSTERS
    )

    # Step 1: Filter by min conviction
    eligible = [e for e in scorable if e.get("conviction_score", 0.0) >= min_conviction]

    if not eligible:
        logger.warning(
            f"[Portfolio] Tidak ada kandidat dengan conviction >= {min_conviction:.0%}. "
            "Fallback ke semua scorable tanpa conviction filter."
        )
        eligible = list(scorable)

    # Step 2: Greedy sector + cluster cap — iterate sorted-by-score, accept jika
    # slot sektor MAUPUN cluster tersedia (cluster cap dilewati bila None atau
    # sektornya tidak dipetakan ke cluster manapun).
    sector_counts: dict[str, int] = {}
    cluster_counts: dict[str, int] = {}
    selected: list[dict] = []
    overflow: list[dict] = []  # ditolak karena sektor/cluster cap; kandidat fallback

    for entry in eligible:
        sector = entry.get("sector_key", "unknown")
        cluster = clusters.get(sector)

        sector_count = sector_counts.get(sector, 0)
        cluster_count = cluster_counts.get(cluster, 0) if cluster is not None else 0
        cluster_has_room = (
            max_per_cluster is None
            or cluster is None
            or cluster_count < max_per_cluster
        )

        if sector_count < max_per_sector and cluster_has_room:
            selected.append(entry)
            sector_counts[sector] = sector_count + 1
            if cluster is not None:
                cluster_counts[cluster] = cluster_count + 1
        else:
            overflow.append(entry)

        if len(selected) >= top_n:
            break

    # Step 3: Soft-cap fallback — jika selected < top_n karena sector/cluster cap ketat
    if len(selected) < top_n and overflow:
        deficit = top_n - len(selected)
        fallback_entries = overflow[:deficit]
        logger.warning(
            f"[Portfolio] Soft-cap fallback: menambahkan {len(fallback_entries)} kandidat "
            f"(total sector/cluster counts akan melebihi max_per_sector={max_per_sector}"
            f"{f', max_per_cluster={max_per_cluster}' if max_per_cluster is not None else ''}). "
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
        f"(top_n={top_n}, sector_cap={max_per_sector}, cluster_cap={max_per_cluster}, "
        f"min_conviction={min_conviction:.0%})"
    )
    return selected
