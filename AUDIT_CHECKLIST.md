# Risk & Valuation Audit — Implementation Checklist

> Source: `AUDIT_RISK_VALUATION.md` · Date: 2026-06-20
> Order: P0 (sekarang) → P8 (nanti) · Setiap task terkoneksi ke issue asalnya

---

## Urutan Prioritas

```
P0 → P1 → P2 → P3 → P4 → P5 → P6 → P7 → P8
 ↑         ↑    ↑                        ↑
 Wajib     Butuh P0   Bebas              Butuh P1
```

---

## 🔴 HARUS DIKERJAKAN SEKARANG (Critical — bisa deploy salah tanpa ini)

### P0 — Fail-Closed Risk Governor
**File:** `core/orchestrator/legacy.py:4905–4921`
**Issue:** GOVERNOR ISSUE 1
**Waktu:** ~30 menit

- [ ] Bungkus `annotate_risk(entry)` dalam `try/except` di `_annotate_risk_governor()`
- [ ] Jika exception → set `entry["risk_governor"] = {..., "sizing_allowed": False, "reason_codes": ["governor_error"]}`
- [ ] Ganti kondisi di `_risk_holds()` dari `is not False` → `is True`
  - BEFORE: `if not isinstance(risk, dict) or risk.get("sizing_allowed") is not False:`
  - AFTER: `if isinstance(risk, dict) and risk.get("sizing_allowed") is True:`
- [ ] Tulis/jalankan test: exception di `annotate_risk()` → entry masuk `holds` (bukan lolos)

> **Kenapa pertama:** Ini satu-satunya bug yang bisa mematikan SEMUA governance dalam satu pipeline run.
> Harus selesai sebelum P1 karena P1 menghasilkan `risk_overvalued=None` yang butuh governor berjalan benar.

---

### P1 — FV Unmeasurable = Unknown, Bukan Safe
**File:** `schemas/debate.py:308–317` + `core/risk_governor.py:411–418`
**Issue:** FV ISSUE 1
**Waktu:** ~45 menit
**Butuh:** P0 selesai dulu (governor harus fail-closed sebelum kita lempar `None` ke dalamnya)

- [ ] Di `CIOVerdict._derive_computed_fields()`: ganti `self.risk_overvalued = False` → `self.risk_overvalued = None` di branch `else`
- [ ] Di `_risk_overvalued_flag()` (`core/risk_governor.py:411–418`): tambah handling `None` → blok sebagai conditional (bukan lolos)
- [ ] Pastikan `is_overvalued` juga ikut di-update dengan nilai `None`
- [ ] Test: FV = None → `risk_overvalued` = None → governor block deployment

> **Koneksi ke P0:** P1 mengubah output menjadi `None`; tanpa P0, governor masih fail-open
> dan `None` tidak akan dievaluasi sama sekali.

---

## 🟠 DIKERJAKAN SETELAH P0-P1 (Medium — celah nyata tapi tidak langsung bencana)

### P2 — `low_confidence` Blok Sizing
**File:** `core/risk_governor.py:34–41`
**Issue:** R/R ISSUE 1
**Waktu:** ~5 menit
**Bebas:** Tidak butuh P0/P1 selesai dulu

- [ ] Tambahkan `"low_confidence"` ke dalam set `HARD_REJECT_CODES`
- [ ] Verifikasi: confidence < 0.60 sudah menghasilkan `low_confidence` di `reason_codes` (cek baris 454–455)
- [ ] Test: BUY dengan confidence=0.35 → `sizing_allowed=False`

> **Koneksi ke P0:** Setelah P0, `low_confidence` di `reason_codes` tidak akan terbuang
> saat exception — fix ini baru benar-benar efektif.

---

### P3 — Selalu Recompute R/R dari Harga, Bukan Percaya LLM
**File:** `core/risk_governor.py:463–465`
**Issue:** R/R ISSUE 2
**Waktu:** ~15 menit
**Bebas:** Tidak butuh P0/P1

- [ ] Balik urutan: panggil `_recompute_rr(ticker, entry_high, target_price, stop_loss)` lebih dulu
- [ ] Gunakan `verdict.risk_reward_ratio` hanya sebagai fallback jika `_recompute_rr()` return `None`
- [ ] Test: LLM memberikan R/R=5.0 tapi harga nyata → R/R=1.8 → nilai 1.8 yang dipakai

> **Koneksi ke P4:** P3 memastikan R/R yang dihitung sudah benar sebelum P4
> mengevaluasi apakah target sudah collapse.

---

### P4 — Early Rejection saat Target Collapsed
**File:** `services/debate_chamber.py:3784–3788`
**Issue:** TARGET ISSUE 3
**Waktu:** ~15 menit
**Sebaiknya:** Kerjakan setelah P3 (R/R sudah benar dulu)

- [ ] Ganti fallback `_next_tick_above(entry_high)` dengan return dict `{"rejected": True, "reason": "target_collapsed: ..."}`
- [ ] Pastikan caller `_compute_trade_envelope()` menghandle key `"rejected": True` dan tidak meneruskan ke debate
- [ ] Test: FV sangat dekat dengan entry → `_compute_trade_envelope()` return rejected → tidak ada debate cycle

> **Koneksi ke GOVERNOR ISSUE 1:** Jika P0 belum selesai, trade collapsed ini bisa lolos
> governor. P4 hanya efektif penuh setelah P0 fix.

---

### P5 — Portfolio Circuit Breaker (Daily Loss ≥ 3% → Halt)
**File:** `core/risk_governor.py` (fungsi baru), `core/orchestrator/legacy.py` (pre-check baru)
**Issue:** GOVERNOR ISSUE 2
**Waktu:** ~2 jam
**Butuh:** P0 selesai (governor harus reliable dulu sebelum ditambah feature baru)

- [ ] Buat fungsi `_portfolio_circuit_breaker(portfolio_state: dict) -> bool`
- [ ] Tambahkan `CIRCUIT_BREAKER_DAILY_LOSS_PCT = 0.03` sebagai konstanta di settings/governor
- [ ] Panggil circuit breaker sebelum `_annotate_risk_governor()` di pipeline
- [ ] Jika breaker aktif → semua entry dapat `sizing_allowed=False` dengan `reason_codes=["circuit_breaker"]`
- [ ] Test: realized_loss_today = 4% → semua ticker blocked

> **Koneksi ke P0 & GOVERNOR ISSUE 3:** Circuit breaker adalah layer portfolio-level;
> GOVERNOR ISSUE 3 (sector concentration) bisa ditambahkan bersamaan di task ini sebagai ekstensi.

---

## 🟡 DIKERJAKAN NANTI (Low-Medium — celah tapi tidak genting)

### P6 — Gate 52-Week High: Hanya Pakai jika ≤ 130% Current Price
**File:** `services/debate_chamber.py:3761–3762`
**Issue:** TARGET ISSUE 1
**Waktu:** ~10 menit
**Bebas:** Tidak ada dependency

- [ ] Tambahkan kondisi `and high_52w <= current_price * 1.30` ke branch `elif high_52w >= target_candidate`
- [ ] Jika 52W high di luar jangkauan → baseline R/R target tetap dipakai (tidak di-overwrite)
- [ ] Test: high_52w = 200%, current_price = 100% → 52W high diabaikan; high_52w = 120% → dipakai

> **Koneksi ke P4:** Setelah P6, 52W high yang stale tidak lagi memicu range yang akhirnya
> collapse di P4's fallback path.

---

### P7 — Sector-Aware `MAX_TARGET_RETURN`
**File:** `services/debate_chamber.py:3643`, `3779`
**Issue:** TARGET ISSUE 2
**Waktu:** ~30 menit
**Bebas:** Tapi lebih baik kerjakan setelah P6 (target logic sudah bersih dulu)

- [ ] Buat dict `_SECTOR_MAX_TARGET = {"mining": 0.20, "consumer": 0.12, "property": 0.15, "bank": 0.10, "default": 0.12}`
- [ ] Di `__init__` atau saat `_compute_trade_envelope()`: resolve `MAX_TARGET_RETURN` dari sektor ticker
- [ ] Pastikan ada mekanisme resolve sektor (dari fundamentals payload atau ticker prefix)
- [ ] Test: BYAN (mining) → cap 20%; BBCA (bank) → cap 10%

> **Koneksi ke P3 & P4:** Setelah P7, R/R mining tidak lagi borderline 1.5×
> (P3 akan menghitung R/R yang lebih realistis, P4 tidak perlu fallback untuk mining).

---

### P8 — `HISTORICALLY_EXPENSIVE` Jadi Soft Reason Code di Governor
**File:** `core/risk_governor.py` (tambah soft gate), `services/context_pack_builder.py` (sudah ada)
**Issue:** FV ISSUE 2
**Waktu:** ~45 menit
**Butuh:** P1 selesai (governor harus bisa menangani berbagai reason code dengan benar)

- [ ] Di governor, cek apakah `valuation_band_context` mengandung string `"HISTORICALLY_EXPENSIVE"`
- [ ] Jika ya → append `"historically_expensive"` ke `reason_codes` (soft, bukan `HARD_REJECT_CODES`)
- [ ] Pastikan `_is_conditional_setup()` mengenali `"historically_expensive"` → hanya boleh proceed sebagai conditional
- [ ] Update output report untuk menampilkan flag ini
- [ ] Test: stock di 95th percentile PE-nya sendiri → `reason_codes` berisi `"historically_expensive"` → conditional only

> **Koneksi ke P1:** P1 memastikan `risk_overvalued=None` untuk data buruk; P8 melengkapi
> dengan menangkap stock yang datanya ada tapi tetap mahal secara historis.

---

## Ringkasan Koneksi Antar Task

```
P0 (fail-closed governor)
 ├── memungkinkan → P1 (None tidak hilang saat exception)
 ├── memungkinkan → P2 (low_confidence tidak terbuang saat exception)
 ├── prasyarat → P5 (circuit breaker butuh governor reliable)
 └── menghilangkan risiko → P4 (collapsed trade tidak lolos governance)

P1 (risk_overvalued = None)
 └── prasyarat → P8 (governor harus handle reason codes benar)

P3 (always recompute R/R)
 └── memperkuat → P4 (R/R yang benar → collapse detection lebih akurat)
     └── memperkuat → P7 (cap yang tepat → R/R mining tidak lagi borderline)

P6 (52W high gate)
 └── mengurangi → P4 (stale 52W high tidak lagi penyebab target collapse)
```

---

## Status Tracking

| ID | Issue | File | Status | Selesai |
|----|-------|------|--------|---------|
| P0 | Fail-closed governor | `legacy.py:4905–4921` | ✅ Selesai | 2026-06-21 |
| P1 | FV=None → unknown | `schemas/debate.py:308–317` | ✅ Selesai | 2026-06-21 |
| P2 | low_confidence → HARD_REJECT | `risk_governor.py:34–41` | ✅ Selesai | 2026-06-21 |
| P3 | Recompute R/R dari harga | `risk_governor.py:463–465` | ✅ Selesai | 2026-06-21 |
| P4 | Early reject target collapsed | `debate_chamber.py:3784–3788` | ✅ Selesai | 2026-06-21 |
| P5 | Circuit breaker daily loss | `risk_governor.py` (baru) | ✅ Selesai | 2026-06-21 |
| P6 | Gate 52W high ≤ 130% | `debate_chamber.py:3761–3762` | ✅ Selesai | 2026-06-21 |
| P7 | Sector-aware cap | `debate_chamber.py:3643,3779` | ✅ Selesai | 2026-06-21 |
| P8 | HISTORICALLY_EXPENSIVE soft gate | `risk_governor.py` (baru) | ✅ Selesai | 2026-06-21 |
