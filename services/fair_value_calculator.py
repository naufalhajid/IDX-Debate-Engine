"""
fair_value_calculator.py — Pure-Python fair value engine untuk saham IHSG.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from utils.logger_config import logger


# ---------------------------------------------------------------------------
# Data container — diisi dari response API Stockbit keystats
# ---------------------------------------------------------------------------

@dataclass
class KeyStats:
    """
    Nilai-nilai fundamental yang dibutuhkan untuk kalkulasi.
    Semua field punya default 0.0 / None agar tidak crash saat data parsial.
    """

    ticker: str = ""

    # Income statement
    eps_ttm: float = 0.0          # Earnings Per Share (Trailing Twelve Months)
    eps_forward: float = 0.0      # EPS proyeksi tahun depan (jika tersedia)
    dps: float = 0.0              # Dividend Per Share (TTM)

    # Balance sheet
    book_value_per_share: float = 0.0   # Ekuitas / jumlah saham beredar

    # Profitability
    roe: float = 0.0              # Return on Equity (desimal: 0.22 = 22%)
    net_margin: float = 0.0       # Net Profit Margin (desimal)
    roa: float = 0.0

    # Market
    current_price: float = 0.0
    shares_outstanding: float = 0.0    # lembar saham beredar (dalam unit, bukan miliar)

    # Historical P/E dan P/B (rata-rata 3-5 tahun, hardcode per sektor atau ambil dari API)
    # Default ini adalah nilai historis konservatif untuk sektor perbankan IHSG
    historical_pe_avg: float = 18.0    # rata-rata P/E historis 5 tahun
    historical_pb_avg: float = 3.5     # rata-rata P/B historis 5 tahun

    # Cost of equity untuk DDM/Gordon Growth (dalam desimal)
    cost_of_equity: float = 0.10       # 10% — default untuk IHSG large cap
    growth_rate: float = 0.07          # 7% — proyeksi pertumbuhan laba jangka panjang

    # Sumber data mentah (untuk debugging)
    raw_pe_current: float = 0.0
    raw_pb_current: float = 0.0


# ---------------------------------------------------------------------------
# Extractor — parse response JSON dari Stockbit keystats API
# ---------------------------------------------------------------------------

def _parse_stockbit_flat(api_response: dict) -> dict[str, str]:
    """
    Flatten the Stockbit /keystats/ratio/v1/{ticker} response into a simple
    {field_name: raw_value_string} dict.

    Actual API structure (confirmed from live response):
        data.closure_fin_items_results[i]
            .fin_name_results[j]
                .fitem.name   → human-readable field name  (e.g. "Current EPS (TTM)")
                .fitem.value  → raw string value            (e.g. "312.50" or "9.96%")

    This flat dict is then consumed by extract_keystats via _lookup().
    Logging which fields were found makes it easy to add new mappings later.
    """
    flat: dict[str, str] = {}
    try:
        groups = (
            api_response.get("data", {})
                        .get("closure_fin_items_results", [])
        )
        for group in groups:
            for item in group.get("fin_name_results", []):
                fitem = item.get("fitem", {})
                name  = fitem.get("name", "").strip()
                value = fitem.get("value", "")
                if name and value not in (None, "", "-", "N/A"):
                    flat[name] = str(value)
    except Exception as e:
        logger.warning("[FairValue] _parse_stockbit_flat failed: {}", e)

    logger.debug("[FairValue] Stockbit flat fields found: {}", list(flat.keys()))
    return flat


def _clean_numeric(raw: str) -> float:
    """
    Convert a raw Stockbit value string to float.
    Handles: "312.50", "9.96%", "Rp 2.530", "1,234.56", "-21.35"
    Returns 0.0 on failure.
    """
    if not raw:
        return 0.0
    # Strip currency prefix and whitespace
    s = re.sub(r"[Rr][Pp]\.?\s*", "", raw).strip()
    # Remove thousand-separators (dot or comma before 3 digits)
    s = re.sub(r"[,.](?=\d{3}(?!\d))", "", s)
    # Remove trailing % sign (caller decides whether to divide by 100)
    s = s.replace("%", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


def extract_keystats(api_response: dict, ticker: str = "") -> KeyStats:
    """
    Ekstrak field yang relevan dari response raw Stockbit keystats API.

    Mendukung DUA struktur API:
      A) closure_fin_items_results (confirmed live format, 2025-2026)
         data.closure_fin_items_results[].fin_name_results[].fitem.{name, value}
      B) Legacy flat key-value (kept as fallback)

    Debug log menampilkan field mana yang berhasil di-parse sehingga mudah
    menambah mapping baru jika Stockbit mengubah nama field.
    """
    stats = KeyStats(ticker=ticker)

    # ── Strategy A: parse by field name (live Stockbit format) ────────────────
    flat = _parse_stockbit_flat(api_response)
    flat_lower = {k.lower(): v for k, v in flat.items()}

    def _lookup(name_patterns: list[str], pct: bool = False) -> float:
        """
        Find the first matching name from flat dict.

        Matching order:
          1. Exact match (original key as Stockbit returns it)
          2. Case-insensitive exact match
          3. Case-insensitive partial match (pattern contained in key)
        Robust terhadap variasi nama field antar versi API dan sektor
        (bank vs mining). ROE BBCA pakai "Return on Equity (TTM)"
        (lowercase 'on') — exact match miss, partial match berhasil.

        pct=True: divide by 100 if value > 1 (normalise percent to decimal).
        """
        for pattern in name_patterns:
            # 1. Exact
            val_str = flat.get(pattern)
            if val_str is None:
                # 2. Case-insensitive exact
                val_str = flat_lower.get(pattern.lower())
            if val_str is None:
                # 3. Case-insensitive partial — pattern is substring of a key
                pl = pattern.lower()
                partial_matches = [(k, v) for k, v in flat_lower.items() if pl in k]
                if partial_matches:
                    # Prefer the shortest key (most specific match)
                    val_str = min(partial_matches, key=lambda x: len(x[0]))[1]
            if val_str is not None:
                v = _clean_numeric(val_str)
                if pct and v > 1.0:
                    v = v / 100.0
                return v
        return 0.0

    if flat:
        # ── Per-share data ───────────────────────────────────────────────────
        stats.eps_ttm = _lookup([
            "Current EPS (TTM)", "EPS (TTM)", "EPS TTM",
            "Earnings Per Share (TTM)",
        ])

        # If EPS is missing but PE and price are available, back-calculate:
        #   EPS = price / PE
        if stats.eps_ttm == 0.0:
            pe_ttm = _lookup(["Current PE Ratio (TTM)", "PE Ratio (TTM)", "Current PE Ratio (Annualised)"])
            if pe_ttm > 0:
                # EPS back-calc dilakukan di build_fair_value_report setelah current_price tersedia
                stats.raw_pe_current = pe_ttm

        stats.eps_forward = _lookup([
            "Forward EPS", "EPS (Forward)", "Estimated EPS",
        ]) or stats.eps_ttm  # fallback to TTM

        stats.book_value_per_share = _lookup([
            "Book Value Per Share", "BVPS", "Book Value/Share",
            "Current Book Value Per Share",
        ])

        stats.dps = _lookup([
            "Dividend Per Share (TTM)", "DPS (TTM)", "Dividend Per Share",
            "DPS", "Annual Dividend Per Share", "Cash Dividend Per Share",
            "Total Dividend Per Share", "Dividen Per Saham",
        ])

        # ── Profitability ratios ─────────────────────────────────────────────
        stats.roe = _lookup([
            "Return On Equity (TTM)", "ROE (TTM)", "ROE", "Return on Equity",
            "Return On Equity", "Return on Equity (TTM)",
            "Imbal Hasil Ekuitas",  # Bahasa Indonesia variant
        ], pct=True)

        stats.net_margin = _lookup([
            "Net Profit Margin (TTM)", "Net Margin (TTM)", "Net Margin",
            "Net Profit Margin", "Profit Margin",
        ], pct=True)

        stats.roa = _lookup([
            "Return On Assets (TTM)", "ROA (TTM)", "ROA", "Return on Assets",
        ], pct=True)

        # ── Valuation multiples ──────────────────────────────────────────────
        stats.raw_pe_current = stats.raw_pe_current or _lookup([
            "Current PE Ratio (TTM)", "PE Ratio (TTM)",
            "Current PE Ratio (Annualised)", "P/E Ratio",
        ])

        stats.raw_pb_current = _lookup([
            "Current Price to Book Value", "Price to Book Value",
            "P/B Ratio", "Price/Book",
        ])

    # ── Strategy B: legacy flat key-value fallback ────────────────────────────
    # Only runs if Strategy A found nothing useful (flat dict empty or all zeros)
    if not flat or (stats.eps_ttm == 0 and stats.book_value_per_share == 0 and stats.dps == 0):
        logger.info("[FairValue] {}: closure_fin_items structure empty, trying legacy key-value", ticker)

        def _get_legacy(keys: list[str], default: float = 0.0) -> float:
            for key in keys:
                try:
                    val = api_response
                    for part in key.split("."):
                        val = val[part]
                    if val is not None:
                        return float(val)
                except (KeyError, TypeError, ValueError):
                    continue
            # Shallow sub-dict search — satu level dalam dari top-level api_response
            simple_keys = {k.split(".")[-1].lower() for k in keys}
            for top_val in api_response.values():
                if not isinstance(top_val, dict):
                    continue
                for k, v in top_val.items():
                    if k.lower() in simple_keys and v is not None:
                        try:
                            return float(v)
                        except (ValueError, TypeError):
                            continue
            return default

        stats.eps_ttm            = _get_legacy(["eps", "eps_ttm", "earningPerShare", "data.Current.EPS", "EPS"])
        stats.book_value_per_share = _get_legacy(["bookValuePerShare", "bvps", "data.Current.BVPS", "BVPS"])
        stats.dps                = _get_legacy(["dps", "dividendPerShare", "data.Current.DPS", "DPS"])
        stats.roe                = _get_legacy(["roe", "returnOnEquity", "data.Current.ROE", "ROE"])
        stats.net_margin         = _get_legacy(["netMargin", "net_margin", "data.Current.NetProfitMargin"])
        stats.roa                = _get_legacy(["roa", "returnOnAssets", "data.Current.ROA"])
        stats.raw_pe_current     = _get_legacy(["pe", "priceEarnings", "data.Current.PE", "PE"])
        stats.raw_pb_current     = _get_legacy(["pb", "priceBook", "data.Current.PBV", "PBV"])

        if stats.roe > 1.0:
            stats.roe = stats.roe / 100.0
        if stats.net_margin > 1.0:
            stats.net_margin = stats.net_margin / 100.0
        if stats.roa > 1.0:
            stats.roa = stats.roa / 100.0

    # ── Derive DPS from dividend yield × price if DPS still missing ──────────
    # BBCA dan bank besar lain kadang tidak expose DPS langsung di keystats
    # tapi selalu expose Dividend Yield (%). DPS = yield × current_price / 100
    if stats.dps == 0.0 and flat:
        div_yield_pct = _lookup([
            "Dividend Yield (TTM)", "Dividend Yield", "Yield",
            "Trailing Dividend Yield", "Dividend Yield (Annual)",
        ])
        if div_yield_pct > 0:
            price_for_dps = stats.current_price or _lookup([
                "Last Price", "Current Price", "Close Price"
            ])
            if price_for_dps > 0:
                # yield dari Stockbit dalam % (e.g. "5.62") → bagi 100
                stats.dps = round((div_yield_pct / 100.0) * price_for_dps, 2) \
                    if div_yield_pct > 1.0 \
                    else round(div_yield_pct * price_for_dps, 2)
                logger.info(
                    "[FairValue] {}: DPS derived from yield "
                    "({:.2f}%) × price ({:,.0f}) "
                    "= {:.2f}",
                    ticker,
                    div_yield_pct,
                    price_for_dps,
                    stats.dps,
                )

    # ── Debug summary ─────────────────────────────────────────────────────────
    parsed = {
        "eps_ttm": stats.eps_ttm,
        "bvps":    stats.book_value_per_share,
        "dps":     stats.dps,
        "roe":     f"{stats.roe * 100:.1f}%",
        "pe":      stats.raw_pe_current,
        "pb":      stats.raw_pb_current,
    }
    zeros = [k for k, v in parsed.items() if str(v) in ("0", "0.0", "0.0%")]
    if zeros:
        logger.warning(
            "[FairValue] {}: fields still 0 after parse: {}. "
            "Add the missing Stockbit field name to the mapping list in extract_keystats().",
            ticker,
            zeros,
        )
    else:
        logger.info("[FairValue] {}: all key stats parsed OK: {}", ticker, parsed)

    return stats


# ---------------------------------------------------------------------------
# Main calculator
# ---------------------------------------------------------------------------

class FairValueCalculator:
    """
    Menghitung fair value saham IHSG menggunakan 3 metode:
      1. P/E Band   — EPS × historical average P/E
      2. P/B Band   — BVPS × historical average P/B
      3. DDM / Gordon Growth Model — untuk saham dengan dividen stabil

    Hasil akhir adalah weighted average yang dapat dikonfigurasi per sektor.
    """

    # Bobot default per metode (harus jumlah = 1.0)
    # Untuk bank (BBCA, BBRI, BMRI): P/B lebih relevan karena aset berbasis ekuitas
    SECTOR_WEIGHTS = {
        "bank":        {"pe": 0.35, "pb": 0.45, "ddm": 0.20},
        "consumer":    {"pe": 0.50, "pb": 0.30, "ddm": 0.20},
        "mining":      {"pe": 0.60, "pb": 0.30, "ddm": 0.10},
        "property":    {"pe": 0.30, "pb": 0.55, "ddm": 0.15},
        "default":     {"pe": 0.45, "pb": 0.35, "ddm": 0.20},
    }

    # Ticker → sektor mapping untuk emiten populer IHSG
    TICKER_SECTOR = {
        "BBCA": "bank", "BBRI": "bank", "BMRI": "bank",
        "BBNI": "bank", "BRIS": "bank", "BTPS": "bank",
        "TLKM": "default", "ASII": "default",
        "UNVR": "consumer", "ICBP": "consumer", "MYOR": "consumer",
        "ADRO": "mining", "BYAN": "mining", "MDKA": "mining",
        "BSDE": "property", "SMRA": "property",
    }

    def __init__(self, stats: KeyStats, sector: str | None = None):
        self.stats = stats
        self.sector = sector or self.TICKER_SECTOR.get(stats.ticker.upper(), "default")
        self.weights = self.SECTOR_WEIGHTS[self.sector]
        self._weighted_result_cache: dict | None = None
        assert abs(sum(self.weights.values()) - 1.0) < 1e-9, (
            f"SECTOR_WEIGHTS['{self.sector}'] tidak menjumlah 1.0: {self.weights}"
        )

    def _cache_weighted_result(self, result: dict) -> dict:
        self._weighted_result_cache = result
        return result

    # ── Metode 1: P/E Band ───────────────────────────────────────────────────

    def fair_value_pe(self) -> float | None:
        """
        Fair value = EPS_TTM × historical_pe_avg
        """
        eps = self.stats.eps_ttm or self.stats.eps_forward
        if eps <= 0 or self.stats.historical_pe_avg <= 0:
            return None
        return round(eps * self.stats.historical_pe_avg, 0)

    # ── Metode 2: P/B Band ───────────────────────────────────────────────────

    def fair_value_pb(self) -> float | None:
        """
        Fair value = BVPS × historical_pb_avg
        """
        bvps = self.stats.book_value_per_share
        if bvps <= 0 or self.stats.historical_pb_avg <= 0:
            return None
        return round(bvps * self.stats.historical_pb_avg, 0)

    # ── Metode 3: DDM (Gordon Growth Model) ─────────────────────────────────

    def fair_value_ddm(self) -> float | None:
        """
        Fair value = DPS / (cost_of_equity - growth_rate)
        """
        dps = self.stats.dps
        ke = self.stats.cost_of_equity
        g = self.stats.growth_rate

        if dps <= 0:
            return None
        if ke <= g:
            return None  # model tidak valid
        if ke - g < 0.03:
            return None  # spread < 3% → DDM too sensitive to be reliable

        fv = dps / (ke - g)

        if self.stats.current_price > 0:
            ratio = fv / self.stats.current_price
            if ratio > 10.0 or ratio < 0.1:
                return None  # outlier — abaikan

        return round(fv, 0)

    # ── Weighted Average ─────────────────────────────────────────────────────

    def fair_value_weighted(self) -> dict:
        pe_fv  = self.fair_value_pe()
        pb_fv  = self.fair_value_pb()
        ddm_fv = self.fair_value_ddm()

        results = {}
        if pe_fv is not None:
            results["pe"] = pe_fv
        if pb_fv is not None:
            results["pb"] = pb_fv
        if ddm_fv is not None:
            results["ddm"] = ddm_fv

        if not results:
            return self._cache_weighted_result({
                "fair_value": None,
                "breakdown": {},
                "confidence": "INSUFFICIENT_DATA",
                "margin_of_safety_pct": None,
                "valuation_verdict": "DATA_UNAVAILABLE",
            })

        total_weight = sum(self.weights[m] for m in results)
        weighted_fv = sum(
            results[m] * (self.weights[m] / total_weight)
            for m in results
        )
        weighted_fv = round(weighted_fv, 0)

        n = len(results)
        confidence = "HIGH" if n == 3 else ("MEDIUM" if n == 2 else "LOW")

        mos = None
        verdict = "DATA_UNAVAILABLE"
        if self.stats.current_price > 0 and weighted_fv > 0:
            mos = round(
                ((weighted_fv - self.stats.current_price) / self.stats.current_price) * 100,
                1
            )
            if mos >= 20:
                verdict = "UNDERVALUED"
            elif mos >= 5:
                verdict = "SLIGHTLY_UNDERVALUED"
            elif mos >= -5:
                verdict = "FAIRLY_VALUED"
            elif mos >= -20:
                verdict = "SLIGHTLY_OVERVALUED"
            else:
                verdict = "OVERVALUED"

        return self._cache_weighted_result({
            "fair_value": weighted_fv,
            "breakdown": {k: int(v) for k, v in results.items()},
            "confidence": confidence,
            "margin_of_safety_pct": mos,
            "valuation_verdict": verdict,
        })

    # ── Target & Stop Calculator ─────────────────────────────────────────────

    @staticmethod
    def calculate_trade_levels(
        entry_low: float,
        entry_high: float,
        target_gain_pct: float = 7.0,
        stop_loss_pct: float = 4.0,
    ) -> dict:
        entry_mid    = (entry_low + entry_high) / 2
        target_price = round(entry_mid * (1 + target_gain_pct / 100), -1)
        stop_loss    = round(entry_mid * (1 - stop_loss_pct / 100), -1)

        gain_rp = target_price - entry_mid
        loss_rp = entry_mid - stop_loss
        rr = round(gain_rp / loss_rp, 2) if loss_rp > 0 else 0.0

        return {
            "entry_mid":          round(entry_mid, 0),
            "target_price":       target_price,
            "stop_loss":          stop_loss,
            "expected_return_pct": f"+{target_gain_pct:.1f}%",
            "risk_reward_ratio":  rr,
        }

    # ── Build Report String (untuk diinjeksi ke raw_data) ───────────────────

    def build_report(self, current_price: float | None = None) -> str:
        if current_price is not None and current_price != self.stats.current_price:
            self._weighted_result_cache = None
        if current_price is not None:   # ← fix: `if current_price:` is False for 0.0
            self.stats.current_price = current_price

        result = self._weighted_result_cache or self.fair_value_weighted()
        fv     = result["fair_value"]
        bdown  = result["breakdown"]
        mos    = result["margin_of_safety_pct"]
        conf   = result["confidence"]
        verdict = result["valuation_verdict"]

        lines = [
            "╔══════════════════════════════════════════════════════════════╗",
            "║  FAIR VALUE REPORT                                           ║",
            "╚══════════════════════════════════════════════════════════════╝",
            "",
            f"TICKER          : {self.stats.ticker}",
            f"SEKTOR          : {self.sector.upper()}",
            f"HARGA PASAR     : Rp {self.stats.current_price:,.0f}",
            "",
            "── BREAKDOWN FAIR VALUE ────────────────────────────────────────",
        ]

        if "pe" in bdown:
            lines.append(
                f"  Metode P/E Band : EPS Rp {self.stats.eps_ttm:,.0f} × "
                f"P/E historis {self.stats.historical_pe_avg:.1f}x "
                f"= Rp {bdown['pe']:,}"
            )
        else:
            lines.append("  Metode P/E Band : TIDAK VALID (EPS = 0 atau data tidak tersedia)")

        if "pb" in bdown:
            lines.append(
                f"  Metode P/B Band : BVPS Rp {self.stats.book_value_per_share:,.0f} × "
                f"P/B historis {self.stats.historical_pb_avg:.1f}x "
                f"= Rp {bdown['pb']:,}"
            )
        else:
            lines.append("  Metode P/B Band : TIDAK VALID (BVPS = 0 atau data tidak tersedia)")

        if "ddm" in bdown:
            lines.append(
                f"  Metode DDM      : DPS Rp {self.stats.dps:,.0f} / "
                f"(ke {self.stats.cost_of_equity*100:.0f}% - g {self.stats.growth_rate*100:.0f}%) "
                f"= Rp {bdown['ddm']:,}"
            )
        else:
            lines.append("  Metode DDM      : TIDAK VALID")

        fv_str = f"Rp {fv:,.0f}" if fv is not None else "Tidak dapat dikalkulasi (Data Kosong / None)"
        lines += [
            "",
            "── HASIL AKHIR ─────────────────────────────────────────────────",
            f"  FAIR VALUE (weighted avg) : {fv_str}",
            f"  Kalkulasi confidence      : {conf} ({len(bdown)}/3 metode valid)",
            "",
        ]

        if mos is not None:
            symbol = "⬆ UPSIDE" if mos >= 0 else "⬇ PREMIUM"
            lines += [
                "── MARGIN OF SAFETY ────────────────────────────────────────────",
                f"  Harga Pasar   : Rp {self.stats.current_price:,.0f}",
                f"  Fair Value    : Rp {fv:,.0f}",
                f"  Gap           : {mos:+.1f}% ({symbol})",
                f"  Verdict       : {verdict}",
                "",
            ]

            if verdict in ("OVERVALUED", "SLIGHTLY_OVERVALUED"):
                premium = abs(mos)
                lines += [
                    "🚨 PERINGATAN OVERVALUATION 🚨",
                    f"   Harga pasar {premium:.1f}% DI ATAS fair value.",
                    "   IMPLIKASI SWING TRADE:",
                    "   • Margin of safety NEGATIF — tidak ada bantalan jika tesis salah.",
                    "   • Entry hanya valid jika ada momentum kuat dan katalis spesifik.",
                    "   • CIO HARUS memberikan rating HOLD atau AVOID kecuali ada alasan",
                    "     teknikal yang sangat kuat untuk override.",
                    "",
                ]
            elif verdict == "UNDERVALUED":
                lines += [
                    "✅ MARGIN OF SAFETY POSITIF",
                    f"   Harga pasar {abs(mos):.1f}% DI BAWAH fair value.",
                    "   Setup swing trade punya bantalan fundamental yang kuat.",
                    "",
                ]

        lines += [
            "── KEY FUNDAMENTALS ────────────────────────────────────────────",
            f"  EPS TTM         : Rp {self.stats.eps_ttm:,.0f}",
            f"  BVPS            : Rp {self.stats.book_value_per_share:,.0f}",
            f"  DPS             : Rp {self.stats.dps:,.0f}",
            f"  ROE             : {self.stats.roe * 100:.1f}%",
            f"  Net Margin      : {self.stats.net_margin * 100:.1f}%",
            f"  P/E saat ini    : {self.stats.raw_pe_current:.1f}x "
                f"(hist avg: {self.stats.historical_pe_avg:.1f}x)",
            f"  P/B saat ini    : {self.stats.raw_pb_current:.1f}x "
                f"(hist avg: {self.stats.historical_pb_avg:.1f}x)",
            "",
            "CATATAN: Semua angka di atas dihitung Python dari data API.",
            "         LLM DILARANG menimpa atau menghitung ulang FAIR VALUE.",
            "═" * 65,
        ]

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Historical P/E & P/B defaults — override ini per emiten jika punya data lebih akurat
# ---------------------------------------------------------------------------

HISTORICAL_MULTIPLES: dict[str, dict] = {
    "BBCA": {"pe": 25.0, "pb": 4.5, "cost_of_equity": 0.09, "growth_rate": 0.07},
    "BBRI": {"pe": 14.0, "pb": 2.2, "cost_of_equity": 0.10, "growth_rate": 0.06},
    "BMRI": {"pe": 13.0, "pb": 1.8, "cost_of_equity": 0.10, "growth_rate": 0.06},
    "BBNI": {"pe": 10.0, "pb": 1.3, "cost_of_equity": 0.11, "growth_rate": 0.05},
    "TLKM": {"pe": 18.0, "pb": 3.0, "cost_of_equity": 0.09, "growth_rate": 0.05},
    "ASII": {"pe": 14.0, "pb": 1.8, "cost_of_equity": 0.10, "growth_rate": 0.06},
    "UNVR": {"pe": 35.0, "pb": 20.0, "cost_of_equity": 0.09, "growth_rate": 0.05},
    "ICBP": {"pe": 20.0, "pb": 3.5, "cost_of_equity": 0.09, "growth_rate": 0.07},
    "GOTO": {"pe":  0.0, "pb": 3.0, "cost_of_equity": 0.12, "growth_rate": 0.15},  
    "ADRO": {"pe": 8.0,  "pb": 1.5, "cost_of_equity": 0.12, "growth_rate": 0.03},
    "BYAN": {"pe": 7.0,  "pb": 3.5, "cost_of_equity": 0.12, "growth_rate": 0.02},
    "BSDE": {"pe": 10.0, "pb": 0.7, "cost_of_equity": 0.11, "growth_rate": 0.05},
}


def get_historical_multiples(ticker: str) -> dict:
    return HISTORICAL_MULTIPLES.get(ticker.upper(), {
        "pe": 15.0, "pb": 2.0, "cost_of_equity": 0.10, "growth_rate": 0.06
    })


def extract_historical_multiples(api_response: dict, ticker: str) -> dict:
    """Extract 5-year median PE/PB from Stockbit API response.

    Tries multiple common Stockbit API response structures to find
    yearly PE and PB values. Falls back to hardcoded HISTORICAL_MULTIPLES
    if extraction fails or yields insufficient data.
    """
    pe_values: list[float] = []
    pb_values: list[float] = []

    data = api_response.get("data", {})

    # Pattern 1: data.{year}.{metric}  (e.g. data.2024.PE)
    if isinstance(data, dict):
        for key, val in data.items():
            if isinstance(key, str) and key.isdigit() and isinstance(val, dict):
                for pe_key in ("PE", "pe", "PER", "per"):
                    pe = val.get(pe_key)
                    if pe is not None:
                        try:
                            pv = float(pe)
                            if pv > 0:
                                pe_values.append(pv)
                        except (ValueError, TypeError):
                            pass
                        break
                for pb_key in ("PBV", "pbv", "PB", "pb", "PriceBook"):
                    pb = val.get(pb_key)
                    if pb is not None:
                        try:
                            bv = float(pb)
                            if bv > 0:
                                pb_values.append(bv)
                        except (ValueError, TypeError):
                            pass
                        break

    # Pattern 2: data.historicalRatio (list of dicts)
    hist = data.get("historicalRatio", data.get("historical_ratio", []))
    if isinstance(hist, list):
        for entry in hist:
            if not isinstance(entry, dict):
                continue
            pe = entry.get("PE") or entry.get("pe") or entry.get("PER")
            pb = entry.get("PBV") or entry.get("pb") or entry.get("PB")
            if pe is not None:
                try:
                    pv = float(pe)
                    if pv > 0:
                        pe_values.append(pv)
                except (ValueError, TypeError):
                    pass
            if pb is not None:
                try:
                    bv = float(pb)
                    if bv > 0:
                        pb_values.append(bv)
                except (ValueError, TypeError):
                    pass

    # Start with hardcoded defaults, override with API-derived medians
    result = get_historical_multiples(ticker)
    if len(pe_values) >= 3:
        sorted_pe = sorted(pe_values)
        result["pe"] = round(sorted_pe[len(sorted_pe) // 2], 1)
    if len(pb_values) >= 3:
        sorted_pb = sorted(pb_values)
        result["pb"] = round(sorted_pb[len(sorted_pb) // 2], 1)

    return result


# ---------------------------------------------------------------------------
# Convenience factory — satu baris dari API response ke report string
# ---------------------------------------------------------------------------

def build_fair_value_report(
    api_response: dict,
    ticker: str,
    current_price: float,
) -> tuple[str, float | None]:
    multiples = extract_historical_multiples(api_response, ticker)
    stats = extract_keystats(api_response, ticker=ticker)

    if multiples.get("pe") is not None:
        stats.historical_pe_avg = multiples["pe"]
    if multiples.get("pb") is not None:
        stats.historical_pb_avg = multiples["pb"]
    if multiples.get("cost_of_equity") is not None:
        stats.cost_of_equity = multiples["cost_of_equity"]
    if multiples.get("growth_rate") is not None:
        stats.growth_rate = multiples["growth_rate"]

    stats.current_price = current_price

    # ── EPS back-calculation from PE × price ──────────────────────────────
    # The Stockbit closure_fin_items endpoint often includes PE but not EPS
    # directly in the visible section.  If EPS is still 0 but we have PE
    # and the live price, we can back-calculate a reasonable EPS estimate.
    if stats.eps_ttm == 0.0 and stats.raw_pe_current > 0 and current_price > 0:
        stats.eps_ttm = round(current_price / stats.raw_pe_current, 2)
        logger.info(
            "[FairValue] {}: EPS back-calculated from PE "
            "({} / {} = {})",
            ticker,
            current_price,
            stats.raw_pe_current,
            stats.eps_ttm,
        )

    calc   = FairValueCalculator(stats)
    result = calc.fair_value_weighted()
    report = calc.build_report(current_price=current_price)

    fv = result["fair_value"]
    if fv is None:
        logger.warning(
            "[FairValue] {}: fair value tidak dapat dikalkulasi — semua metode gagal",
            ticker,
        )
    return report, fv
