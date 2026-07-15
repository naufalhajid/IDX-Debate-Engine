"""
utils/xlsx_adapter.py — Adapter antara data scraping Stockbit (xlsx) dan FairValueCalculator.

MASALAH YANG DI-SOLVE:
  fair_value_calculator.py sebelumnya hanya bisa menerima input dari Stockbit API
  (hit real-time per ticker). Jika API down atau rate-limit, seluruh pipeline gagal
  dan fair value tidak bisa dikalkulasi.

SOLUSI:
  XlsxDataAdapter membaca file xlsx hasil scraping (957 saham sekaligus) dan
  mengkonversinya ke format KeyStats yang sudah dipakai FairValueCalculator —
  tanpa mengubah satu baris pun di fair_value_calculator.py.

KEUNGGULAN vs API:
  1. Coverage: 957 ticker sekaligus vs 1 ticker per API call.
  2. Metode ke-4 (EV/EBITDA): Data tersedia di xlsx, tidak ada di API endpoint lama.
  3. Historical PE dinamis: Pakai IHSG PE Median (8.83) dari data live, bukan hardcode.
  4. Piotroski F-Score: Quality gate langsung dari xlsx — no extra API call.
  5. Ex-Date: 'Latest Dividend Ex-Date' tersedia, exdate_scanner tidak perlu hit yfinance.
  6. Sentiment: Sheet 'sentiments' bisa dipakai langsung oleh _sentiment_node().

STRUKTUR XLSX:
  Sheet 'key-statistics' : 94 kolom — semua rasio fundamental
  Sheet 'stock-prices'   : harga OHLC, volume, frekuensi
  Sheet 'analysis'       : composite rank, PBV×ROE, discount %
  Sheet 'sentiments'     : ~20k baris konten Stockbit per ticker
  Sheet 'idx-stocks'     : metadata (nama, IPO date, market cap)

USAGE:
  from utils.xlsx_adapter import XlsxDataAdapter

  adapter = XlsxDataAdapter("output/IDX_Fundamental_Analysis_2026-04-24.xlsx")
  report_str, fv_price = adapter.build_fair_value_report("BBRI")

  # Sebagai drop-in replacement build_fair_value_report() di _fundamental_node():
  report_str, fv_price = adapter.build_fair_value_report(ticker, current_price)

  # Akses extra data untuk debate pipeline:
  sentiment_text = adapter.get_sentiment_text("BBRI")
  exdate_info    = adapter.get_exdate_info("BBRI", current_price)
  quality_flags  = adapter.get_quality_flags("BBRI")
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, date, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from fair_value_calculator import KeyStats
    from utils.exdate_scanner import ExDateInfo

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sektor mapping — lebih lengkap dari TICKER_SECTOR di fair_value_calculator.py
# ---------------------------------------------------------------------------

_TICKER_SECTOR: dict[str, str] = {
    # Banking
    "BBCA": "bank",
    "BBRI": "bank",
    "BMRI": "bank",
    "BBNI": "bank",
    "BRIS": "bank",
    "BTPS": "bank",
    "BNGA": "bank",
    "BNII": "bank",
    "PNBN": "bank",
    "BDMN": "bank",
    "MEGA": "bank",
    "AGRO": "bank",
    "BJTM": "bank",
    "BJBR": "bank",
    "NISP": "bank",
    "BABP": "bank",
    # Consumer
    "UNVR": "consumer",
    "ICBP": "consumer",
    "MYOR": "consumer",
    "INDF": "consumer",
    "SIDO": "consumer",
    "CPIN": "consumer",
    "JPFA": "consumer",
    "KLBF": "consumer",
    "DVLA": "consumer",
    "TSPC": "consumer",
    "ULTJ": "consumer",
    "GOOD": "consumer",
    # Mining
    "ADRO": "mining",
    "BYAN": "mining",
    "MDKA": "mining",
    "PTBA": "mining",
    "ITMG": "mining",
    "INCO": "mining",
    "ANTM": "mining",
    "TINS": "mining",
    "HRUM": "mining",
    "DOID": "mining",
    "ELSA": "mining",
    # Property
    "BSDE": "property",
    "SMRA": "property",
    "CTRA": "property",
    "PWON": "property",
    "LPKR": "property",
    "DMAS": "property",
    # Telecom
    "TLKM": "telecom",
    "EXCL": "telecom",
    "ISAT": "telecom",
    # Industrial / Automotive
    "ASII": "industrial",
    "ASTRA": "industrial",
    "AUTO": "industrial",
    "GJTL": "industrial",
    "SMSM": "industrial",
    # Energy
    "PGAS": "energy",
    "MEDC": "energy",
    "AKRA": "energy",
    "LSIP": "energy",
    "AALI": "energy",
    # Tech
    "GOTO": "tech",
    "BUKA": "tech",
    "EMTK": "tech",
    # Healthcare
    "HEAL": "healthcare",
    "MIKA": "healthcare",
    "PRDA": "healthcare",
}

# EV/EBITDA historical multiples per sektor (median 5 tahun IHSG)
_SECTOR_EV_EBITDA: dict[str, float] = {
    "bank": 0.0,  # EV/EBITDA tidak relevan untuk bank
    "consumer": 14.0,
    "mining": 5.0,
    "property": 10.0,
    "telecom": 10.0,
    "industrial": 8.0,
    "energy": 6.0,
    "tech": 20.0,
    "healthcare": 15.0,
    "default": 10.0,
}


# ---------------------------------------------------------------------------
# Main Adapter
# ---------------------------------------------------------------------------


class XlsxDataAdapter:
    """
    Membaca seluruh data xlsx Stockbit sekali (lazy-loaded per sheet) dan
    menyediakan interface yang identik dengan build_fair_value_report() lama.

    Thread-safety: pd.DataFrame bersifat read-only setelah diload — aman
    digunakan dari asyncio tasks yang berbeda tanpa lock.
    """

    def __init__(self, xlsx_path: str | Path):
        self.path = Path(xlsx_path)
        if not self.path.exists():
            raise FileNotFoundError(f"Xlsx tidak ditemukan: {self.path}")

        self._df_keystats: pd.DataFrame | None = None
        self._df_prices: pd.DataFrame | None = None
        self._df_analysis: pd.DataFrame | None = None
        self._df_sentiments: pd.DataFrame | None = None
        self._df_idxstocks: pd.DataFrame | None = None
        logger.info(f"[XlsxAdapter] Initialized dengan: {self.path}")

    # ── Lazy sheet loaders ──────────────────────────────────────────────────

    def _keystats(self) -> pd.DataFrame:
        if self._df_keystats is None:
            self._df_keystats = pd.read_excel(self.path, sheet_name="key-statistics")
            logger.info(
                f"[XlsxAdapter] Loaded key-statistics: {len(self._df_keystats)} tickers"
            )
        return self._df_keystats

    def _prices(self) -> pd.DataFrame:
        if self._df_prices is None:
            self._df_prices = pd.read_excel(self.path, sheet_name="stock-prices")
        return self._df_prices

    def _analysis(self) -> pd.DataFrame:
        if self._df_analysis is None:
            self._df_analysis = pd.read_excel(self.path, sheet_name="analysis")
        return self._df_analysis

    def _sentiments(self) -> pd.DataFrame:
        if self._df_sentiments is None:
            self._df_sentiments = pd.read_excel(self.path, sheet_name="sentiments")
        return self._df_sentiments

    def _idxstocks(self) -> pd.DataFrame:
        if self._df_idxstocks is None:
            self._df_idxstocks = pd.read_excel(self.path, sheet_name="idx-stocks")
        return self._df_idxstocks

    # ── Row lookup helpers ──────────────────────────────────────────────────

    def _ks_row(self, ticker: str) -> pd.Series | None:
        """Ambil satu baris key-statistics untuk ticker. None jika tidak ada."""
        df = self._keystats()
        match = df[df["Ticker"] == ticker.upper()]
        return match.iloc[0] if not match.empty else None

    def _price_row(self, ticker: str) -> pd.Series | None:
        df = self._prices()
        match = df[df["Ticker"] == ticker.upper()]
        return match.iloc[0] if not match.empty else None

    def _analysis_row(self, ticker: str) -> pd.Series | None:
        df = self._analysis()
        match = df[df["Ticker"] == ticker.upper()]
        return match.iloc[0] if not match.empty else None

    # ── Safe value extractor ────────────────────────────────────────────────

    @staticmethod
    def _f(row: pd.Series, col: str, default: float = 0.0) -> float:
        """Ambil nilai float dari Series; return default jika kosong/NaN/invalid."""
        if col not in row.index:
            return default
        val = row[col]
        if val is None or (isinstance(val, float) and math.isnan(val)):
            return default
        if isinstance(val, (int, float, np.integer, np.floating)):
            return float(val)
        try:
            return float(str(val).replace(",", "").strip())
        except (ValueError, TypeError):
            return default

    # ── Historical PE/PB dari data live ─────────────────────────────────────

    def _get_historical_multiples(self, ticker: str, ks: pd.Series) -> dict:
        """
        Tentukan PE/PB historis untuk weighted average.

        Logika prioritas:
          1. Jika ticker ada di HISTORICAL_MULTIPLES (hardcode dari calculator) → pakai itu
             sebagai base, tapi tetap cek apakah IHSG PE Median lebih konservatif.
          2. Jika tidak ada di hardcode → gunakan sektor default + IHSG PE Median
             sebagai batas atas.

        Kenapa batas atas? Karena PE yang di-set terlalu tinggi untuk saham yang
        biasanya ditrade di PE rendah akan menghasilkan fair value yang terlalu
        optimistis. IHSG PE Median (8.83 saat ini) adalah reality-check yang bagus.
        """
        from services.fair_value_calculator import get_historical_multiples

        base = get_historical_multiples(ticker)

        ihsg_pe_median = self._f(ks, "IHSG PE Ratio TTM (Median)", default=0.0)
        current_pe = self._f(ks, "Current PE Ratio (TTM)", default=0.0)
        forward_pe = self._f(ks, "Forward PE Ratio", default=0.0)
        current_pb = self._f(ks, "Current Price to Book Value", default=0.0)

        # Hitung historical PE dari rata-rata 3Y + 5Y price return implied PE
        # Jika tidak ada data historis: fallback ke min(hardcode, current*1.2)
        # Logika: historical average biasanya sedikit di atas current saat market
        # sedang tertekan (seperti saat ini), tapi tidak boleh terlalu jauh.
        computed_pe = base["pe"]
        if ihsg_pe_median > 0 and current_pe > 0:
            # Cap: tidak boleh lebih dari 2× IHSG median (prevent outlier)
            pe_ceiling = ihsg_pe_median * 2.0
            computed_pe = min(base["pe"], pe_ceiling)
            # Jika forward PE tersedia (lebih akurat untuk growth stock)
            if forward_pe > 0 and forward_pe < current_pe:
                # Blended: 60% historical, 40% forward
                computed_pe = (computed_pe * 0.60) + (forward_pe * 0.40)

        # PB: jika current PB jauh di bawah historis hardcode, pakai mid-point
        # (mencerminkan de-rating yang terjadi di IHSG belakangan ini)
        computed_pb = base["pb"]
        if current_pb > 0 and computed_pb > 0:
            if current_pb < computed_pb * 0.50:
                # De-rating signifikan — ambil rata-rata current dan historis
                computed_pb = (current_pb + computed_pb) / 2.0
                logger.debug(
                    f"[XlsxAdapter] {ticker}: PB de-rating detected, "
                    f"adjusted from {base['pb']:.1f}x to {computed_pb:.1f}x"
                )

        return {
            "pe": round(computed_pe, 1),
            "pb": round(computed_pb, 1),
            "cost_of_equity": base["cost_of_equity"],
            "growth_rate": base["growth_rate"],
        }

    # ── KeyStats builder ─────────────────────────────────────────────────────

    def extract_keystats(self, ticker: str, current_price: float = 0.0) -> "KeyStats":
        """
        Baca data dari xlsx dan kembalikan KeyStats — identik dengan
        extract_keystats(api_response, ticker) dari fair_value_calculator.py.

        Tidak ada fallback ke API — jika ticker tidak ada di xlsx,
        kembalikan KeyStats default (semua 0.0) dengan warning log.
        """
        from services.fair_value_calculator import KeyStats

        ks = self._ks_row(ticker)
        if ks is None:
            logger.warning(
                f"[XlsxAdapter] {ticker} tidak ditemukan di xlsx. "
                f"Returning empty KeyStats."
            )
            return KeyStats(ticker=ticker, current_price=current_price)

        f = self._f  # alias pendek

        # Ambil harga dari xlsx price sheet jika current_price tidak disediakan
        if current_price == 0.0:
            pr = self._price_row(ticker)
            if pr is not None:
                current_price = f(pr, "Price") or f(pr, "Close Price")

        stats = KeyStats(
            ticker=ticker,
            # ── Income statement ──
            eps_ttm=f(ks, "Current EPS (TTM)"),
            # Forward EPS: pakai jika tersedia (11% ticker), fallback ke TTM
            eps_forward=f(ks, "Forward PE Ratio")
            and (
                current_price / f(ks, "Forward PE Ratio")
                if f(ks, "Forward PE Ratio") > 0
                else f(ks, "Current EPS (TTM)")
            )
            or f(ks, "Current EPS (TTM)"),
            dps=f(ks, "Dividend (TTM)"),
            # ── Balance sheet ──
            book_value_per_share=f(ks, "Current Book Value Per Share"),
            # ── Profitability ──
            roe=f(ks, "Return on Equity (TTM)"),  # sudah dalam desimal (0.17)
            net_margin=f(ks, "Net Profit Margin (Quarter)"),  # sudah dalam desimal
            roa=f(ks, "Return on Assets (TTM)"),  # sudah dalam desimal
            # ── Cash flow ──
            operating_cash_flow_ttm=f(ks, "Cash From Operations (TTM)"),
            # ── Market ──
            current_price=current_price,
            shares_outstanding=f(ks, "Current Share Outstanding"),
            # ── Valuation saat ini (untuk display di report) ──
            raw_pe_current=f(ks, "Current PE Ratio (TTM)"),
            raw_pb_current=f(ks, "Current Price to Book Value"),
        )

        # Set historical multiples dari logika dinamis
        multiples = self._get_historical_multiples(ticker, ks)
        stats.historical_pe_avg = multiples["pe"]
        stats.historical_pb_avg = multiples["pb"]
        stats.cost_of_equity = multiples["cost_of_equity"]
        stats.growth_rate = multiples["growth_rate"]

        # Normalise — xlsx sudah dalam desimal untuk ROE/margin, tapi jaga-jaga
        if stats.roe > 1.0:
            stats.roe = stats.roe / 100.0
        if stats.net_margin > 1.0:
            stats.net_margin = stats.net_margin / 100.0
        if stats.roa > 1.0:
            stats.roa = stats.roa / 100.0

        return stats

    # ── Metode ke-4: EV/EBITDA ──────────────────────────────────────────────

    def fair_value_ev_ebitda(
        self, ticker: str, current_price: float = 0.0
    ) -> float | None:
        """
        Fair value ke-4: EV/EBITDA Band.

        Formula:
          FV = (EBITDA_TTM × Sektor_EV_EBITDA_historis + Net_Debt) / Shares

        Tidak dipakai untuk bank (EV/EBITDA tidak relevan karena struktur modal).
        Valid jika EBITDA TTM > 0 dan shares outstanding > 0.
        """
        sektor = _TICKER_SECTOR.get(ticker.upper(), "default")
        if sektor == "bank":
            return None

        target_ev_ebitda = _SECTOR_EV_EBITDA.get(sektor, _SECTOR_EV_EBITDA["default"])
        if target_ev_ebitda <= 0:
            return None

        ks = self._ks_row(ticker)
        if ks is None:
            return None

        f = self._f
        ebitda_ttm = f(ks, "EBITDA (TTM)")
        net_debt = f(ks, "Net Debt (Quarter)")
        shares = f(ks, "Current Share Outstanding")

        if ebitda_ttm <= 0 or shares <= 0:
            return None

        enterprise_value = ebitda_ttm * target_ev_ebitda
        equity_value = enterprise_value - net_debt
        if equity_value <= 0:
            return None

        fv = equity_value / shares

        # Sanity check: tidak boleh > 10× atau < 0.1× current price
        if current_price > 0:
            ratio = fv / current_price
            if ratio > 10.0 or ratio < 0.1:
                return None

        return round(fv, 0)

    # ── Drop-in replacement untuk build_fair_value_report() ─────────────────

    def build_fair_value_report(
        self,
        ticker: str,
        current_price: float = 0.0,
    ) -> tuple[str, float | None]:
        """
        Drop-in replacement untuk build_fair_value_report(api_response, ticker, price)
        dari fair_value_calculator.py.

        Perbedaan dari versi API:
          - Data dari xlsx (tidak hit API)
          - Historical PE lebih dinamis (pakai IHSG PE Median sebagai cap)
          - Tambah quality flags (Piotroski, Altman) di output report

        Valuasi (weighted average, per-method bridge, data-quality gate) memakai
        services.fair_value_calculator._build_fair_value_core() — engine kanonik
        yang sama dipakai jalur API, sehingga FV dan keputusan quality gate
        identik untuk fundamental yang identik (FIX 1: no independent math here).
        EV/EBITDA murni dikendalikan oleh SECTOR_WEIGHTS kanonik (mining-only),
        bukan toggle lokal.

        Returns:
            (report_str, fair_value_float)
            fair_value_float = None jika semua metode gagal ATAU quality gate menolak.
        """
        from services.fair_value_calculator import _build_fair_value_core

        stats = self.extract_keystats(ticker, current_price)
        if current_price == 0.0:
            current_price = stats.current_price

        # FIX 1 (sector-resolution parity): do NOT pass xlsx's own hardcoded
        # _TICKER_SECTOR guess as an override. output/sector_cache.json (957
        # tickers, yfinance-derived) is both more complete and more accurate
        # than xlsx's local ~60-ticker table (e.g. LSIP/AALI are agriculture,
        # not "energy"; SIDO/KLBF are healthcare, not "consumer") — letting
        # FairValueCalculator resolve sector the same way it does for the API
        # path (sector_cache -> small TICKER_SECTOR -> "default") means both
        # sources select the same SECTOR_WEIGHTS for the same ticker, not just
        # the same aggregation math.
        _canonical_report, result = _build_fair_value_core(
            stats, ticker, current_price, sector=None
        )
        # 5-bucket sector the engine actually used (bank/consumer/mining/
        # property/default) — for report display and the EV/EBITDA text
        # below, not xlsx's local guess which may have just been overridden.
        sektor = result.get("sector") or _TICKER_SECTOR.get(ticker.upper(), "default")

        # Quality flags untuk enrichment report (xlsx-only, tidak ada di API)
        quality = self.get_quality_flags(ticker)
        bdown = result.get("breakdown", {})

        report_str = self._build_extended_report(
            stats=stats,
            sektor=sektor,
            result=result,
            pe_fv=bdown.get("pe"),
            pb_fv=bdown.get("pb"),
            ddm_fv=bdown.get("ddm"),
            ev_fv=bdown.get("ev_ebitda"),
            quality=quality,
            current_price=current_price,
        )

        fv = result.get("fair_value")
        return report_str, fv

    def _build_extended_report(
        self,
        stats: "KeyStats",
        sektor: str,
        result: dict,
        pe_fv: float | None,
        pb_fv: float | None,
        ddm_fv: float | None,
        ev_fv: float | None,
        quality: dict,
        current_price: float,
    ) -> str:
        """Build laporan teks yang diperluas — lebih informatif dari versi API."""
        fv = result["fair_value"]
        bdown = result["breakdown"]
        mos = result["margin_of_safety_pct"]
        conf = result["confidence"]
        verdict = result["valuation_verdict"]

        n_methods = result.get("active_method_count", len(bdown))
        max_methods = result.get(
            "configured_active_method_count",
            4 if ev_fv is not None and sektor != "bank" else 3,
        )

        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  FAIR VALUE REPORT — Dihitung Python dari Data Stockbit xlsx ║",
            "║  Gunakan angka ini VERBATIM. Jangan menghitung ulang.        ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            f"TICKER          : {stats.ticker}",
            f"SEKTOR          : {sektor.upper()}",
            f"HARGA PASAR     : Rp {current_price:,.0f}",
            "DATA SOURCE     : xlsx scraping (bukan API real-time)",
            "",
            "── BREAKDOWN FAIR VALUE ────────────────────────────────────────",
        ]

        # Metode 1: PE
        if pe_fv:
            lines.append(
                f"  Metode 1 P/E Band   : EPS Rp {stats.eps_ttm:,.0f} × "
                f"P/E historis {stats.historical_pe_avg:.1f}x = Rp {int(pe_fv):,}"
            )
        else:
            lines.append(
                f"  Metode 1 P/E Band   : TIDAK VALID "
                f"(EPS={stats.eps_ttm:.0f} atau PE historis={stats.historical_pe_avg:.1f})"
            )

        # Metode 2: PB
        if pb_fv:
            lines.append(
                f"  Metode 2 P/B Band   : BVPS Rp {stats.book_value_per_share:,.0f} × "
                f"P/B historis {stats.historical_pb_avg:.1f}x = Rp {int(pb_fv):,}"
            )
        else:
            lines.append(
                f"  Metode 2 P/B Band   : TIDAK VALID "
                f"(BVPS={stats.book_value_per_share:.0f})"
            )

        # Metode 3: DDM
        if ddm_fv:
            lines.append(
                f"  Metode 3 DDM        : DPS Rp {stats.dps:,.0f} / "
                f"(ke {stats.cost_of_equity * 100:.0f}% − g {stats.growth_rate * 100:.0f}%) "
                f"= Rp {int(ddm_fv):,}"
            )
        else:
            reason = (
                "DPS=0/tidak tersedia"
                if not stats.dps or stats.dps <= 0
                else "spread ke−g terlalu kecil atau outlier"
            )
            lines.append(f"  Metode 3 DDM        : TIDAK VALID ({reason})")

        # Metode 4: EV/EBITDA
        if sektor != "bank":
            if ev_fv:
                ev_mult = _SECTOR_EV_EBITDA.get(sektor, _SECTOR_EV_EBITDA["default"])
                lines.append(
                    f"  Metode 4 EV/EBITDA  : EBITDA × {ev_mult:.0f}x historical "
                    f"→ equity value / shares = Rp {int(ev_fv):,}"
                )
            else:
                lines.append(
                    "  Metode 4 EV/EBITDA  : TIDAK VALID (EBITDA=0 atau data tidak tersedia)"
                )
        else:
            lines.append(
                "  Metode 4 EV/EBITDA  : SKIP (tidak relevan untuk sektor bank)"
            )

        # Hasil akhir
        fv_str = f"Rp {int(fv):,}" if fv else "Tidak dapat dikalkulasi"
        lines += [
            "",
            "── HASIL AKHIR ─────────────────────────────────────────────────",
            f"  FAIR VALUE (weighted avg) : {fv_str}",
            f"  Kalkulasi confidence      : {conf} "
            f"({n_methods}/{max_methods} metode aktif)",
            "",
        ]

        # FIX 1: quality-gate rejection must be as visible on the xlsx report
        # as it already is on the API report — same canonical gate, same
        # transparency, regardless of which source produced the fundamentals.
        if result.get("fv_quality_rejected"):
            reasons = ", ".join(result.get("fv_quality_reasons", []))
            lines += [
                f"⚠️ FAIR VALUE QUALITY GATE: estimasi FV di atas TIDAK dipakai "
                f"sebagai anchor valuasi ({reasons}). Jangan mengutip FV atau "
                "valuation gap sebagai fakta; perlakukan valuasi sebagai UNKNOWN.",
                "",
            ]

        # Margin of safety
        if mos is not None and fv:
            symbol = "⬆ UPSIDE" if mos >= 0 else "⬇ PREMIUM"
            lines += [
                "── MARGIN OF SAFETY ────────────────────────────────────────────",
                f"  Harga Pasar   : Rp {current_price:,.0f}",
                f"  Fair Value    : Rp {int(fv):,}",
                f"  Gap           : {mos:+.1f}% ({symbol})",
                f"  Verdict       : {verdict}",
                "",
            ]
            if verdict in ("OVERVALUED", "SLIGHTLY_OVERVALUED"):
                lines += [
                    f"🚨 PERINGATAN OVERVALUATION: Harga {abs(mos):.1f}% DI ATAS fair value.",
                    "   Entry swing trade hanya valid jika ada momentum & katalis spesifik.",
                    "   CIO HARUS HOLD atau AVOID kecuali ada alasan teknikal sangat kuat.",
                    "",
                ]
            elif verdict == "UNDERVALUED":
                lines += [
                    f"✅ MARGIN OF SAFETY POSITIF: Harga {abs(mos):.1f}% DI BAWAH fair value.",
                    "   Setup swing trade punya bantalan fundamental yang kuat.",
                    "",
                ]

        # Key fundamentals
        lines += [
            "── KEY FUNDAMENTALS ────────────────────────────────────────────",
            f"  EPS TTM         : Rp {stats.eps_ttm:,.0f}",
            f"  BVPS            : Rp {stats.book_value_per_share:,.0f}",
            f"  DPS             : Rp {(stats.dps or 0.0):,.0f}",
            f"  ROE             : {stats.roe * 100:.1f}%",
            f"  Net Margin      : {stats.net_margin * 100:.1f}%",
            f"  ROA             : {stats.roa * 100:.1f}%",
            f"  P/E saat ini    : {stats.raw_pe_current:.1f}x  "
            f"(hist avg dipakai: {stats.historical_pe_avg:.1f}x)",
            f"  P/B saat ini    : {stats.raw_pb_current:.1f}x  "
            f"(hist avg dipakai: {stats.historical_pb_avg:.1f}x)",
            "",
        ]

        # Quality flags
        pf = quality.get("piotroski_f_score", "N/A")
        altman = quality.get("altman_z", "N/A")
        pf_label = (
            "🟢 STRONG (≥7)"
            if isinstance(pf, (int, float)) and pf >= 7
            else "🟡 MEDIUM (4–6)"
            if isinstance(pf, (int, float)) and pf >= 4
            else "🔴 WEAK (<4)"
            if isinstance(pf, (int, float))
            else "N/A"
        )
        lines += [
            "── QUALITY FLAGS ───────────────────────────────────────────────",
            f"  Piotroski F-Score  : {pf} {pf_label}",
            f"  Altman Z-Score     : {altman if isinstance(altman, str) else f'{altman:.2f}'}",
        ]

        if quality.get("warning"):
            lines.append(f"  ⚠️  {quality['warning']}")

        lines += [
            "",
            "CATATAN: Semua angka dihitung Python dari xlsx scraping Stockbit.",
            "         LLM DILARANG menimpa atau menghitung ulang FAIR VALUE.",
            "═" * 65,
        ]

        return "\n".join(lines)

    # ── Quality Flags ────────────────────────────────────────────────────────

    def get_quality_flags(self, ticker: str) -> dict:
        """
        Kembalikan quality flags dari xlsx — tidak perlu API call tambahan.

        Returns dict dengan:
          piotroski_f_score : int 0-9  (≥7 = STRONG, 4-6 = MEDIUM, <4 = WEAK)
          altman_z          : float    (>2.6 = SAFE, 1.1-2.6 = GREY, <1.1 = DISTRESS)
          is_distressed     : bool     True jika Altman Z < 1.1
          is_weak_quality   : bool     True jika F-Score < 4
          warning           : str | None
        """
        ks = self._ks_row(ticker)
        if ks is None:
            return {
                "piotroski_f_score": None,
                "altman_z": None,
                "is_distressed": False,
                "is_weak_quality": False,
                "warning": None,
            }

        f = self._f
        pf = f(ks, "Piotroski F-Score")
        altman = f(ks, "Altman Z-Score (Modified)")

        warnings = []
        if pf < 4:
            warnings.append(f"F-Score LEMAH ({int(pf)}/9) — kualitas fundamental buruk")
        if 0 < altman < 1.1:
            warnings.append(
                f"Altman Z {altman:.2f} — zona DISTRESS (risiko kebangkrutan)"
            )
        elif 1.1 <= altman < 2.6:
            warnings.append(f"Altman Z {altman:.2f} — zona GREY (perlu monitoring)")

        return {
            "piotroski_f_score": int(pf) if pf > 0 else 0,
            "altman_z": altman,
            "is_distressed": 0 < altman < 1.1,
            "is_weak_quality": pf < 4,
            "warning": " | ".join(warnings) if warnings else None,
        }

    # ── Sentiment Text ───────────────────────────────────────────────────────

    def get_sentiment_text(self, ticker: str, max_posts: int = 20) -> str:
        """
        Kembalikan teks sentimen dari sheet 'sentiments' siap diinjeksi ke
        _sentiment_node() sebagai pengganti Stockbit API stream.

        Membersihkan mention-tags [%XxXx%] agar LLM fokus ke konten.
        """
        df = self._sentiments()
        rows = df[df["Ticker"] == ticker.upper()].dropna(subset=["Content"])
        rows = rows[rows["Content"].str.strip().str.len() > 5]

        if rows.empty:
            return f"Tidak ada data sentimen untuk {ticker} di xlsx."

        rows = rows.head(max_posts)

        def _clean(text: str) -> str:
            text = re.sub(r"\[%[A-Za-z0-9]+%\]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text

        lines = [f"=== SENTIMEN STOCKBIT — {ticker} ({len(rows)} posts) ==="]
        for _, row in rows.iterrows():
            content = _clean(str(row.get("Content", "")))
            posted = str(row.get("Posted At", ""))[:10]
            category = (
                str(row.get("Category", "")) if pd.notna(row.get("Category")) else ""
            )
            cat_tag = f" [{category}]" if category and category != "nan" else ""
            lines.append(f"[{posted}]{cat_tag} {content}")

        return "\n".join(lines)

    # ── Ex-Date dari xlsx (tanpa hit yfinance) ───────────────────────────────

    def get_exdate_info(self, ticker: str, current_price: float = 0.0) -> "ExDateInfo":
        """
        Ambil ex-dividend date langsung dari xlsx — tidak perlu yfinance.

        Field 'Latest Dividend Ex-Date' di xlsx berisi tanggal terakhir ex-date
        (format: '21 Apr 26'). Kita cek apakah tanggal ini upcoming atau sudah lewat.

        Jika sudah lewat → CLEAR (tidak ada upcoming ex-date).
        Jika upcoming     → hitung days_until dan assign risk tier.

        Ini menggantikan scan_exdate() dari exdate_scanner.py untuk ticker
        yang ada di xlsx, menghilangkan satu yfinance API call per ticker.
        """
        _CLEAR: ExDateInfo = {
            "has_upcoming_exdate": False,
            "ex_date": None,
            "days_until_exdate": None,
            "div_per_share": None,
            "div_yield_pct": None,
            "risk_tier": "CLEAR",
            "expected_drop_rp": None,
            "source": "xlsx",
        }

        ks = self._ks_row(ticker)
        if ks is None:
            return _CLEAR

        exdate_str = ks.get("Latest Dividend Ex-Date", None)
        if not exdate_str or str(exdate_str).strip() in ("", "-", "nan", "NaT"):
            return _CLEAR

        # Parse tanggal — format Stockbit: '21 Apr 26' atau '24 Apr 26'
        try:
            exdate_str_clean = str(exdate_str).strip()
            # Coba parsing '%d %b %y' (21 Apr 26) dan '%d %b %Y' (21 Apr 2026)
            ex_date: date | None = None
            for fmt in ("%d %b %y", "%d %b %Y", "%Y-%m-%d"):
                try:
                    ex_date = datetime.strptime(exdate_str_clean, fmt).date()
                    break
                except ValueError:
                    continue

            if ex_date is None:
                logger.warning(
                    f"[XlsxAdapter] Tidak bisa parse ex-date '{exdate_str}' untuk {ticker}"
                )
                return _CLEAR

            today = datetime.now(timezone.utc).date()
            days_until = (ex_date - today).days

            if days_until < 0:
                return {**_CLEAR, "ex_date": str(ex_date), "source": "xlsx"}

            # Div per share dan yield
            f = self._f
            div_per_share: float | None = f(ks, "Dividend (TTM)") or None
            div_yield_pct: float | None = None
            if div_per_share and current_price > 0:
                div_yield_pct = round((div_per_share / current_price) * 100, 2)

            # Risk tier — sama dengan exdate_scanner.py
            from utils.exdate_scanner import CRITICAL_WINDOW_DAYS, WARNING_WINDOW_DAYS

            if days_until <= CRITICAL_WINDOW_DAYS:
                risk_tier = "CRITICAL"
            elif days_until <= WARNING_WINDOW_DAYS:
                risk_tier = "WARNING"
            else:
                risk_tier = "CLEAR"

            return {
                "has_upcoming_exdate": risk_tier != "CLEAR",
                "ex_date": str(ex_date),
                "days_until_exdate": days_until,
                "div_per_share": div_per_share,
                "div_yield_pct": div_yield_pct,
                "risk_tier": risk_tier,
                "expected_drop_rp": div_per_share,
                "source": "xlsx",
            }

        except Exception as e:
            logger.warning(f"[XlsxAdapter] get_exdate_info error untuk {ticker}: {e}")
            return _CLEAR

    # ── Batch pre-screen ─────────────────────────────────────────────────────

    def screen_tickers(
        self,
        tickers: list[str] | None = None,
        min_piotroski: int = 4,
        exclude_distressed: bool = True,
        exclude_special_monitoring: bool = True,
    ) -> list[str]:
        """
        Filter ticker berdasarkan quality threshold sebelum masuk debate pipeline.
        Dipakai oleh orchestrator sebagai pre-screen sebelum run_batch_debates().

        Args:
            tickers                    : list ticker yang mau di-screen.
                                         None = semua 957 ticker di xlsx.
            min_piotroski              : Exclude jika F-Score < nilai ini.
            exclude_distressed         : Exclude jika Altman Z < 1.1.
            exclude_special_monitoring : Exclude saham PEMANTAUAN KHUSUS (suspend-risk).

        Returns:
            List ticker yang lolos filter, sorted by Composite Rank desc.
        """
        df_ks = self._keystats()
        if tickers:
            df_ks = df_ks[df_ks["Ticker"].isin([t.upper() for t in tickers])]

        mask = pd.Series([True] * len(df_ks), index=df_ks.index)

        if min_piotroski > 0:
            mask &= df_ks["Piotroski F-Score"] >= min_piotroski

        if exclude_distressed:
            altman = df_ks["Altman Z-Score (Modified)"]
            # Exclude hanya jika altman > 0 dan < 1.1 (0 = data tidak ada)
            mask &= ~((altman > 0) & (altman < 1.1))

        if exclude_special_monitoring:
            df_idx = self._idxstocks()
            special = set(
                df_idx[df_idx["Note"].str.contains("PEMANTAUAN", na=False)][
                    "Ticker"
                ].tolist()
            )
            mask &= ~df_ks["Ticker"].isin(special)

        # Sort by Composite Rank dari analysis sheet
        df_analysis = self._analysis()
        filtered = df_ks[mask]["Ticker"].tolist()

        try:
            rank_map = dict(zip(df_analysis["Ticker"], df_analysis["Composite Rank"]))
            filtered.sort(key=lambda t: rank_map.get(t, 0), reverse=True)
        except Exception:
            pass

        logger.info(
            f"[XlsxAdapter] screen_tickers: {len(filtered)}/{len(df_ks)} lolos filter "
            f"(piotroski≥{min_piotroski}, distressed={exclude_distressed})"
        )
        return filtered

    # ── Utility: apakah ticker ada di xlsx ──────────────────────────────────

    def has_ticker(self, ticker: str) -> bool:
        return self._ks_row(ticker) is not None

    def get_current_price_from_xlsx(self, ticker: str) -> float:
        """Ambil harga terkini dari stock-prices sheet."""
        pr = self._price_row(ticker)
        if pr is None:
            return 0.0
        return self._f(pr, "Price") or self._f(pr, "Close Price")

    def list_tickers(self) -> list[str]:
        return self._keystats()["Ticker"].tolist()
