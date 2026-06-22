# Calculation Audit — Task Checklist

> Source: `CALCULATION_AUDIT.md` | Date: 2026-06-22  
> Order: C1 (paling kritis) -> C12 (nice-to-have)  
> Setiap task: file + line yang berubah + waktu estimasi

---

## Urutan Prioritas

```
C1 -> C3 -> C5 -> C6   (formula salah -- harus fix sebelum deploy)
C2                      (regulatory -- perlu riset dulu, baru fix)
C4 -> C8               (missing calculations)
C7 -> C9               (calibration)
C10 -> C11 -> C12      (minor / documentation)
```

---

## CRITICAL -- Formula Salah (Fix Sebelum Deploy)

### C1 -- ATR: Ganti SMA jadi Wilder's EWM
**File:** `utils/technicals.py:55`  
**Severity:** HIGH -- Stop placement salah setelah setiap volatility spike  
**Waktu:** 5 menit

**Dampak terverifikasi (TLKM-like, Rp 3.200):**
- ATR SMA 5 hari setelah spike: Rp 65.2 vs ATR Wilder yang benar: Rp 52.0 (25% terlalu lebar)
- Stop SMA: Rp 3.137 vs Stop Wilder: Rp 3.170 (beda Rp 33/saham, Rp 3.296/lot)
- Selama 14 hari setelah spike, SMA-ATR konsisten terlalu lebar -- R/R dilaporkan lebih buruk dari realita

**Perubahan:**
- [ ] Ganti `return tr.rolling(window).mean()` jadi `return tr.ewm(alpha=1/window, min_periods=window, adjust=False).mean()`

**Kenapa pertama:** Semua stop-loss di `pipeline.py:798` mewarisi error ini. Setiap trade setelah market volatil punya stop placement yang salah.

---

### C3 -- Sharpe: Perbaiki faktor annualisasi per-trade
**File:** `core/backtester/metrics_calculator.py:111`  
**Severity:** HIGH -- Sharpe dilaporkan 2.6x-3.2x terlalu tinggi  
**Waktu:** 15 menit

**Dampak terverifikasi (mean=3.5%, std=4.2%):**
- Current code: `IR x sqrt(252)` = Sharpe 13.2 (salah)
- Correct H=7 hari: `IR x sqrt(252/7)` = Sharpe 5.0
- Current code 2.6x overstated vs H=7, 3.2x vs H=10

**Perubahan:**
- [ ] Tambah `IDX_SWING_AVG_HOLD_DAYS = 10` sebagai konstanta di file yang sama
- [ ] Ganti `math.sqrt(252)` jadi `math.sqrt(252 / IDX_SWING_AVG_HOLD_DAYS)`
- [ ] Update comment: jelaskan H=10 adalah asumsi konservatif untuk IDX swing

Note: Jika `ClosedTrade` punya field `holding_days`, gunakan average aktual alih-alih konstanta.

---

### C5 -- Bollinger Bands: ddof=1 -> ddof=0
**File:** `utils/technicals.py:215`  
**Severity:** MEDIUM -- Band 2.6% lebih lebar dari spec Bollinger (1983); squeeze detection bias  
**Waktu:** 2 menit

**Dampak terverifikasi (BBCA-like, N=20):**
- Sample std (current): Rp 66.38 vs Population std (correct): Rp 64.70
- BB Upper: Rp 9.806.9 vs Rp 9.803.5 (beda Rp 3.4/band)
- Ratio tepat sqrt(20/19) = 1.026 -- systematic, bukan random

**Perubahan:**
- [ ] Ganti `close.rolling(period).std()` jadi `close.rolling(period).std(ddof=0)`

---

### C6 -- Volume Surge Ratio: Keluarkan hari ini dari average
**File:** `core/quant_filter/pipeline.py:636`  
**Severity:** MEDIUM -- 9.1% understatement sistematis; borderline tier1 bisa salah klasifikasi  
**Waktu:** 5 menit

**Dampak terverifikasi (ASII, 150M spike di atas baseline 50M/hari):**
- Current code surge ratio: 2.73x (menyertakan hari ini)
- Correct surge ratio: 3.00x (hanya prior 20 hari)
- Setup di 2.1x asli bisa dilaporkan sebagai 1.91x (di bawah tier1 threshold 2.0x)

**Perubahan:**
- [ ] Ganti `float(vol.tail(20).mean())` jadi `float(vol.iloc[-21:-1].mean()) if len(vol) >= 21 else float(vol.iloc[:-1].mean())`
- [ ] Apply fix yang sama untuk `adt_20` jika ingin konsistensi penuh

---

## REGULATORY -- Perlu Riset Dulu

### C2 -- ARB Threshold: Verifikasi Rule BEI Terkini
**File:** `core/quant_filter/pipeline.py:185`  
**Severity:** HIGH -- Konflik antara comment code (-15%) dan project context (-7%); threshold mungkin unreachable  
**Waktu:** 30 menit (riset) + 10 menit (implementasi)

**Konflik yang terdeteksi:**
- Code comment `pipeline.py:171`: "ARB post-April 2025 IDX rule: -15% single day"
- Project context notes: "ARB limit: -7% flat post-2024"
- Code HIGH threshold: -12%, MEDIUM: -7%

**Jika ARB = -7% (project context benar):**
- Threshold HIGH -12% tidak pernah bisa tercapai (saham ARB duluan di -7%)
- Semua saham yang terkena ARB hanya muncul sebagai MEDIUM, tidak pernah HIGH
- Threshold yang benar: HIGH di -6.0%, MEDIUM di -4.0%

**Jika ARB = -15% (code comment benar):**
- Threshold HIGH -12% sudah benar (early warning sebelum limit)
- MEDIUM -7% sudah benar

**Langkah:**
- [ ] Cek Surat Edaran BEI terbaru mengenai batas ARB (idx.co.id atau OJK)
- [ ] Update nilai `HIGH_ARB_PCT` dan `MEDIUM_ARB_PCT` di `core/quant_filter/pipeline.py:185`
- [ ] Update comment agar konsisten dengan nilai aktual yang terverifikasi

---

## MISSING CALCULATIONS

### C4 -- Target Price: Tambah Validasi Batas ARA
**File:** `core/risk_governor.py` (tambah fungsi baru)  
**Severity:** HIGH -- Target di atas batas ARA tidak pernah diflagging  
**Waktu:** 30 menit

**IDX ARA limits (post-April 2025):**
- Price < Rp 200: +35%
- Price Rp 200 - 5.000: +25%
- Price > Rp 5.000: +20%

**Contoh:** Saham Rp 3.000 dengan target Rp 4.050 (+35%) tidak bisa dicapai dalam satu sesi (ARA = +25%). Trade masih valid untuk 2+ sesi, tapi user harus tahu.

**Perubahan:**
- [ ] Tambah helper di `core/risk_governor.py`:
  ```python
  def _ara_sessions_needed(entry: float, target: float) -> int:
      ara = 0.35 if entry < 200 else (0.25 if entry <= 5000 else 0.20)
      if target <= entry:
          return 0
      import math
      return math.ceil(math.log(target / entry) / math.log(1 + ara))
  ```
- [ ] Tambah flag (bukan hard-reject) di `_evaluate_candidate()` jika sessions_needed > SWING_HORIZON_DAYS (default 5)
- [ ] Sertakan `ara_sessions_needed` di output risk governor untuk transparansi

---

### C8 -- T+2 Settlement: Track Pending Settlement Capital
**File:** `core/quant_filter/position_sizer.py`  
**Severity:** MEDIUM -- Capital recycling dalam satu minggu bisa overcommit cash  
**Waktu:** 45 menit

**Skenario masalah:** Beli BBCA hari Senin, jual Selasa siang (early profit), beli TLKM Selasa sore. Cash dari penjualan BBCA baru settle Kamis, tapi position sizer sudah alokasikan untuk TLKM.

**Perubahan:**
- [ ] Track `pending_settlement: dict[date, float]` di position sizer context
- [ ] Kurangi `available_capital` sebesar nilai unsettled sales sebelum sizing
- [ ] Atau implementasi sebagai soft-flag: "Capital not yet settled -- confirm before execution"

---

## CALIBRATION

### C7 -- R/R Thresholds: Naikkan untuk Kompensasi Transaction Costs
**File:** `core/risk_governor.py`, `utils/trade_math.py`  
**Severity:** MEDIUM -- 1.5x threshold hanya 1.38x net setelah 0.50% round-trip  
**Waktu:** 10 menit

**Dampak (BBCA entry Rp 9.500, stop Rp 9.025, target Rp 10.425):**
- Nominal R/R: 1.95x
- Net R/R setelah 0.50% round-trip: 1.82x
- Setup tepat di 1.50x threshold -> net 1.38x (8% di bawah threshold)

**Perubahan:**
- [ ] Large-cap minimum: 1.3 -> 1.4 (constant: `LARGE_CAP_RR_MINIMUM`)
- [ ] Default minimum: 1.5 -> 1.62 atau 1.65 (constant: `DEFAULT_RR_MINIMUM`)
- [ ] Update constants di `core/risk_governor.py`

---

### C9 -- Risk Budget: Bagi Per-Position, Bukan Portfolio Total
**File:** `core/quant_filter/position_sizer.py:364`  
**Severity:** LOW -- 3 posisi bisa masing-masing consume full budget  
**Waktu:** 5 menit

**Perubahan:**
- [ ] Tambah: `per_position_budget = max_loss_budget / max_positions`
- [ ] Ganti `max_loss_budget` dengan `per_position_budget` di baris `lot_from_risk`

---

## DOCUMENTATION / MINOR

### C10 -- Graham k=18.2: Dokumentasikan Trade-off Sektor
**File:** `core/quant_filter/config.py`  
**Severity:** LOW -- Tidak ada code change, hanya dokumentasi  
**Waktu:** 5 menit

**Dampak terverifikasi (ASII EPS=400, BVPS=5000, Price=5800):**
- k=22.5 (standard): FV = Rp 6.708, gap = +15.7% (tier2)
- k=18.2 (IDX): FV = Rp 6.033, gap = +4.0% (tier3)
- Selisih 10.1% FV, potensial tier misclassification

**Perubahan:**
- [ ] Tambah comment di config.py di atas `graham_k: 18.2`:
  - Nilai 18.2 = 13x P/E x 1.4x P/B (IDX universe median)
  - Konservatif vs standard 22.5 (US: 15x P/E x 1.5x P/B)
  - Consumer/telecom mungkin butuh k lebih tinggi di masa depan

---

### C11 -- DDM: Suppress untuk High-ROE Banks ketika Ke > 14%
**File:** `services/fair_value_calculator.py:877`  
**Severity:** LOW -- 5% weight sudah meminimalkan dampak  
**Waktu:** 10 menit

**Perubahan (opsional):**
- [ ] Di `fair_value_ddm()`, tambah guard setelah existing guards:
  ```python
  if self.stats.roe and self.stats.roe > 0.20 and ke > 0.14:
      return None   # DDM tidak valid untuk high-ROE compounders dengan Ke tinggi
  ```

---

### C12 -- MACD Histogram: Handle Zero Edge Case
**File:** `utils/technicals.py:128`  
**Severity:** LOW -- Float zero sangat jarang; dampak production minimal  
**Waktu:** 5 menit

**Perubahan:**
- [ ] Tambah `elif hist_now == 0: state = "NEUTRAL"` sebelum `else` branch

---

## Progress Tracker

| Task | Status | Selesai |
|---|---|---|
| C1 -- ATR Wilder's | [x] | 2026-06-22 |
| C2 -- ARB BEI verify | [x] | 2026-06-22 |
| C3 -- Sharpe annualisasi | [x] | 2026-06-22 |
| C4 -- ARA target check | [x] | 2026-06-22 |
| C5 -- Bollinger ddof=0 | [x] | 2026-06-22 |
| C6 -- Volume surge fix | [x] | 2026-06-22 |
| C7 -- R/R cost-adjust | [ ] | — |
| C8 -- T+2 settlement | [x] | 2026-06-22 |
| C9 -- Risk budget per-pos | [x] | 2026-06-22 |
| C10 -- Graham k doc | [ ] | — |
| C11 -- DDM suppress | [ ] | — |
| C12 -- MACD zero edge | [ ] | — |

**Quick wins (< 5 menit each, HIGH impact): C1, C5, C6 -- tiga perubahan ini masing-masing satu baris.**

---

## Summary

| Category | Tasks | Estimasi Waktu |
|---|---|---|
| Formula salah | C1, C3, C5, C6 | ~30 menit |
| Regulatory | C2 | ~40 menit |
| Missing calculations | C4, C8 | ~75 menit |
| Calibration | C7, C9 | ~15 menit |
| Dokumentasi/minor | C10, C11, C12 | ~20 menit |
| **Total** | **12** | **~3 jam** |
