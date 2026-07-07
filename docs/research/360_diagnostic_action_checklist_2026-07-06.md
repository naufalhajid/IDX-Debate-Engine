## Action Checklist — Tindak Lanjut Diagnosis 360 (2026-07-06)

Turunan dari `docs/research/360_diagnostic_rejected_stocks_2026-07-06.md`.
Urut prioritas. **Guardrail utama di paling bawah — baca dulu.**

---

### P0 (MERAH) — Ukur EDGE dulu (pakai infra yang SUDAH ADA, jangan bangun ulang)

Infra backtest sudah tersedia + ada **20 snapshot XLSX (23 Apr - 2 Jul)** yang mencakup
tape tenang pra-crash (Apr/Mei) **dan** crash Juni — persis "healthy tape" yang
`diagnostic_gate_reopen` bilang hilang. Tujuannya menjawab: **"sistem ini punya edge
atau tidak?"** — karena 2W/21L (1 crash) bukan sampel.

- [ ] **0.1 Audit infra backtest existing.** Petakan cakupan + gap tiap komponen:
  `scripts/historical_backtest.py` (replay envelope OHLCV-only), `scripts/run_backtest.py`,
  `scripts/backtest_walkforward.py` (forecaster XGBoost, bukan pipeline penuh),
  `core/backtest_outcome_evaluator.py`, `core/backtest_memory.py`.
  -> Output: 1 paragraf per script: apa yang diukur, apa yang TIDAK.
- [ ] **0.2 Jalankan `historical_backtest.py` SEKARANG** (deterministik, tanpa LLM, murah)
  di ~20-30 ticker likuid, 2 tahun -> win rate + avg PnL% + avg holding envelope geometry.
  -> Acceptance: angka edge envelope hitam-di-atas-putih, dibandingkan baseline 2W/21L.
- [ ] **0.3 Bereskan drift konstanta.** `historical_backtest.py` meng-*hardcode mirror*
  konstanta envelope produksi (NOISE_GATE 1.5x, REGIME_ATR 2.5x, dst.). Impor dari sumber
  tunggal (`utils/technicals`, `debate_chamber`) supaya hasil backtest merepresentasikan
  produksi, bukan salinan usang. -> Acceptance: tidak ada lagi konstanta ganda.
- [ ] **0.4 Replay AS-OF pakai 20 snapshot XLSX.** Untuk tiap tanggal snapshot D, jalankan
  `quant_filter` dengan fundamental as-of <= D + OHLCV s/d D (no lookahead), catat entry
  hipotetis, evaluasi vs forward OHLCV. Ini memberi edge **lintas rezim** (tenang vs crash).
  -> Acceptance: win rate & avg R per rezim.
- [ ] **0.5 Konsolidasi metrik + definisi "berhasil".** Win rate, avg R-multiple, max
  drawdown, jumlah trade **per rezim**. Tetapkan ambang lulus di muka (mis. win rate >= 40%
  & avg R >= 1.5 di >= 2 rezim) supaya keputusan tuning berbasis data, bukan perasaan.

Estimasi: 0.2 hari ini (1-2 jam); 0.1/0.3/0.4/0.5 ~1-2 sesi.

---

### P1 (ORANYE) — Putuskan intent, uji paralel (JANGAN flip filosofi buta)

- [ ] **1.1 KEPUTUSAN eksplisit: konservatif vs agresif.**
  - Konservatif -> terima bahwa melewatkan BREN adalah **harga** menghindari crash; 0 BUY
    di tape post-crash = fitur.
  - Agresif -> kejar momentum, terima false-positive (banyak yang crash 50% secepat naik).
  - *Tak bisa dua-duanya.* Trade-off lengkap di doc diagnosis Dimensi 3.
- [ ] **1.2 Jika agresif: uji `experiment/momentum-only` PARALEL** (infra Fase 2 di
  `over_engineering_remediation_checklist.md`), bandingkan **outcome realized forward** vs
  sistem hybrid — bukan ganti langsung.
- [ ] **1.3 Investigasi kenapa `momentum_play` 0x menyala** (fundamental_scout tak FAIL?
  volume breakout tak ter-reconfirm oleh technical scout?). Pakai
  `diagnostic_momentum_floor_bypass.py --debate` saat regime kembali NORMAL/SIDEWAYS.

Estimasi: 1.1 = keputusan (menit); 1.2/1.3 = butuh P0 dulu.

---

### P2 (KUNING) — Data quality + fix spesifik (SETELAH P0)

- [ ] **2.1 Refresh XLSX** (`uv run python main.py`) — XLSX 2 Jul sudah 4 hari (DEGRADED,
  -10 ke semua skor). -> Acceptance: staleness FRESH.
- [ ] **2.2 Feed harga DSSA anomali** (debate pakai px 815 vs harga pasar riil puluhan
  ribu Rp) — cek penyesuaian split/adjust di pipeline harga. -> Acceptance: px DSSA cocok pasar.
- [ ] **2.3 GARCH non-stationary -> fallback ATR klasik.** Log run hari ini:
  `persistence=1.0000` (IGARCH boundary) + "ATR > 3x classic capping" berulang. Saat
  persistence ~ 1, pakai classic ATR (bukan sekadar cap) -> stop lebih sempit -> kurangi
  `rr_too_low` palsu. -> Acceptance: tidak ada lagi spam capping.
- [ ] **2.4 (opsional) Trace BRPT/PTRO borderline** — kenapa `stop_inside_noise`/`rr_too_low`.
  Apakah geometri kelewat ketat, atau memang tak-tradeable? Jangan diubah tanpa P0.

Estimasi: 2.1 = menit; 2.2/2.3 = 0.5 hari each.

---

### GUARDRAIL (STOP) — Definition of Done (baca sebelum sentuh apa pun)

- [ ] **JANGAN longgarkan R/R floor (1.4-1.62) atau preflight/envelope gate sebelum P0
  memberi bukti edge.** Setup R/R ~1.0 yang diblok sekarang **memang** jelek (ATR lebar ->
  stop lebar). Melonggarkan tanpa data = **mengulang 21 kekalahan** (BUY berulang ke
  BMRI/DMAS saat crash Juni).
- [ ] **Tolakan BREN/DSSA (13.5x FV / 5-6% float) adalah BENAR** — bukan item yang perlu
  "diperbaiki". Kalau tergoda "biar nangkap BREN", kembali ke doc diagnosis Dimensi 2.
- [ ] Setiap perubahan gate/threshold harus diikuti `uv run pytest` + re-run backtest P0
  untuk konfirmasi tidak menurunkan edge.
