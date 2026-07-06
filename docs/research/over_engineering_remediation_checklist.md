# Checklist Remediasi — Audit Over-Engineering 2026-07-06

Turunan actionable dari `docs/research/over_engineering_audit_2026-07-06.md` (verdict 29/50, MILDLY OVER-ENGINEERED). Diurutkan berdasarkan dependensi dan risiko: bersih-bersih aman dulu → diagnosa murah → eksperimen inti → keputusan berbasis data → pantauan berkelanjutan.

Update checkbox seiring progres. Jangan lompat ke Fase 2 sebelum Fase 0 selesai — beberapa file yang dihapus di Fase 0 (`arima.py`, dst.) perlu dipastikan tidak diam-diam dirujuk sebelum Fase 2 mengubah jalur eksekusi.

---

## Fase 0 — Bersihkan Kode Mati (aman, cepat, tanpa keputusan, tidak saling bergantung)

*Estimasi: rendah (jam, bukan hari). Nol risiko — semua sudah confirmed nol pemanggil produksi.*

- [x] 0.1 Hapus `core/verification_runner.py` (dev-tool checker, nol pemanggil produksi) — atau pindah ke `scripts/` jika masih ingin dipakai manual — **selesai 2026-07-06**: dihapus, di-regrep ulang segar sebelum hapus (nol importer selain test-nya sendiri)
- [x] 0.2 Hapus `core/agent_eval_harness.py` (golden-case harness, nol pemanggil produksi) — atau pindah ke `tests/` jika konsepnya ingin dipertahankan sebagai testing infra — **selesai 2026-07-06**: dihapus, `ExpectedConsensus`/`EvalCase` dikonfirmasi tidak dirujuk di luar file+test-nya
- [x] 0.3 Hapus `core/tool_registry.py` (registry tool typed, tidak pernah dipakai `debate_chamber.py`) — **selesai 2026-07-06**: dihapus, `ToolSpec` dikonfirmasi tidak dirujuk di luar file+test-nya
- [x] 0.4 Hapus `core/forecasting/models/arima.py` (orphaned, sudah dikeluarkan dari `_return_model_factories()`) — **selesai 2026-07-06**: dihapus; dikonfirmasi `_return_model_factories()` cuma berisi `naive`/`xgboost`, dan `ARIMAForecaster` nol importer repo-wide
- [x] 0.5 Hapus/arsipkan `core/forecasting/models/lstm.py` dan `models/prophet_model.py` (tidak pernah diinstansiasi — `service.py` memalsukan placeholder vote-nya) — **selesai 2026-07-06**: dihapus; dikonfirmasi `service.py::_experimental_unused_votes()` cuma hardcode string literal `"lstm"`/`"prophet"`, tidak pernah impor kelasnya. Catatan: `pyproject.toml` masih mendeklarasikan `prophet>=1.1.5`/`statsmodels>=0.14.0` sebagai dependency (komentar di sana bilang sengaja dipertahankan) — di luar scope 0.5, tidak diubah
- [x] 0.6 Bersihkan test terkait: `tests/test_tool_registry.py`, `tests/test_verification_runner.py`, `tests/test_agent_eval_harness.py` — hapus atau relokasi sesuai keputusan 0.1-0.3 — **selesai 2026-07-06**: ketiganya dihapus (tidak ada test khusus arima/lstm/prophet_model yang perlu dibersihkan — `tests/test_forecasting_service.py` hanya menguji placeholder vote di service.py, tidak mengimpor model-nya, jadi tidak disentuh)
- [x] 0.7 Jalankan `uv run pytest` penuh sebelum dan sesudah — pastikan tidak ada dependensi tersembunyi yang terlewat oleh grep manual — **selesai 2026-07-06**: baseline 1152 passed → setelah hapus 1134 passed, delta 18 persis cocok dengan jumlah `def test_` di 3 file test yang dihapus (4+6+8=18, diverifikasi via `git show HEAD`). `ruff check .` juga clean.

---

## Fase 1 — Diagnostik Cepat (murah, menginformasikan Fase 2, bisa paralel dengan Fase 0)

*Estimasi: rendah-menengah (jam sampai 1 sesi). Ini investigasi, bukan pembangunan fitur baru.*

- [x] 1.1 Audit apakah `services/debate_prompts/chartist.txt` benar-benar memakai field display-only (MACD histogram, pola candlestick, posisi Bollinger, divergensi RSI, gap, kompresi volatilitas) secara bermakna dalam reasoning-nya — kalau tidak, hentikan komputasinya di `quant_filter/pipeline.py` — **selesai 2026-07-06**: chartist.txt (STEP 8/9) memang memakai field ini secara bermakna, TAPI dari komputasi independen `debate_chamber.py:2471-2503` — bukan dari `quant_filter/pipeline.py`. Field versi `pipeline.py` sendiri (Task 10/11/12, baris 1082-1087+1152-1171) dikonfirmasi nol konsumen (`reporting.py` tidak merender, `core/orchestrator/legacy.py` tidak membaca, `debate_chamber.py` mengimpor util yang sama secara independen bukan membaca hasil `pipeline.py`). Dihapus: 6 baris komputasi + ~20 baris entry dict + 6 import util yang jadi unused. Test `test_analyze_ticker_returns_s1_s4_fields` di-rename/disederhanakan jadi `test_analyze_ticker_returns_is_lq45_field` (field mati dibuang dari assertion, `is_lq45` yang genuinely live dipertahankan). `uv run pytest` 1134 passed, `ruff check` clean.
- [x] 1.2 **Prioritas tertinggi di fase ini**: putuskan resolusi kontradiksi preflight-vs-envelope yang sudah didokumentasikan tapi belum diputuskan di `diagnostic_gate_reopen_2026-07-02.md` — opsi: (a) samakan surrogate preflight dengan stop struktural envelope, atau (b) dokumentasikan resmi sebagai "intended double-gate" dengan alasan eksplisit — **selesai 2026-07-06: opsi (b) dipilih** (ditanyakan via AskUserQuestion, user tidak merespons dalam 60 detik, dilanjutkan dengan opsi rekomendasi di bawah Auto Mode). Nol perubahan perilaku — didokumentasikan sebagai docstring di `_run_tradeability_preflight` (`services/debate_chamber.py`) + addendum "Keputusan V1.x" di `diagnostic_gate_reopen_2026-07-02.md`. Rasional: track record realized 2W/21L belum memberi bukti pelonggaran gate menguntungkan. **Belum final dari sisi user** — silakan revisi ke opsi (a) bila tidak setuju.
- [x] 1.3 Trace apakah `USE_GARCH_ATR` dinamis pernah membalik hasil gate (stop → R/R → floor) dibanding classic ATR pada data historis yang ada — kalau tidak pernah terbukti mengubah keputusan, pertimbangkan nonaktifkan default-nya — **selesai 2026-07-06, temuan: TIDAK PERNAH BISA, secara struktural, bukan cuma "belum terbukti"**. Jalur gate nyata (`_run_tradeability_preflight` → `_compute_trade_envelope` → `risk_governor`) mengambil `atr14` dari `_compute_technical_indicators` di `debate_chamber.py:2244`, yang HARDCODE classic `compute_atr()` — tidak pernah memanggil `calculate_dynamic_atr`/GARCH sama sekali. `USE_GARCH_ATR` hanya memengaruhi satu tempat: kolom preview "Stop Loss Level"/"ATR (14)" di `quant_filter/pipeline.py` (tampilan `idx filter`, tidak digerbangi apa pun — dikonfirmasi nol reject berbasis R/R di `_analyze_ticker`). Bahkan preview itu sendiri sudah tidak match persis dengan envelope resmi terlepas dari pilihan ATR (anchor beda: `sma20`-anchored di pipeline.py vs `swing_low`-anchored di envelope) — jadi GARCH-vs-classic cuma memperlebar gap kosmetik yang sudah ada, bukan menciptakan yang baru. **Tidak ada perubahan kode dilakukan** — tidak ada gate untuk diperbaiki. Rekomendasi opsional (belum dieksekusi, silakan diputuskan terpisah bila diinginkan): set `USE_GARCH_ATR=False` supaya preview quant_filter minimal konsisten secara basis ATR dengan envelope resmi — murni kerapian tampilan, bukan perbaikan risiko.
- [x] 1.4 Putuskan peran resmi gap-risk stress metric V4.4 di `position_sizer.py` — saat ini "informational only, pending review" menurut komentar penulisnya sendiri; putuskan enforce jadi lot-cap atau resmikan permanen sebagai informational — **selesai 2026-07-06: diresmikan permanen sebagai informational-only** (ditanyakan via AskUserQuestion, user tidak merespons dalam 60 detik, dilanjutkan dengan opsi rekomendasi). Komentar `GAP_RISK_STRESS_ARB_DAYS` di `position_sizer.py` diupdate dari "pending review" jadi keputusan permanen. Nol perubahan logika/nilai. **Belum final dari sisi user** — silakan minta enforcement lot-cap bila diinginkan (butuh sesi desain ambang terpisah).

---

## Fase 2 — Eksperimen Paralel (pekerjaan inti — rekomendasi tunggal terpenting dari audit)

*Estimasi: setup 1-2 sesi dev; **pengumpulan data butuh waktu kalender berminggu-minggu**, bukan cuma jam kerja — realized outcome perlu waktu untuk resolve.*

- [ ] 2.1 Wiring: pasang `risk_governor.evaluate_risk()` + validasi envelope trade langsung di atas output `services/single_agent_analyzer.py` (saat ini nol pemanggilan ke risk_governor)
- [ ] 2.2 Terapkan resolusi 1.2 secara konsisten ke jalur baru ini juga (jangan mewarisi kontradiksi preflight/envelope ke eksperimen)
- [ ] 2.3 Jalankan kedua jalur (single-agent+governor vs multi-agent `debate_chamber`) paralel pada sampel ticker yang sama, harian/mingguan
- [ ] 2.4 Catat verdict + rasional kedua jalur ke log terpisah per jalur (format serupa `backtest_memory.jsonl`, tag jalur `single_agent` vs `multi_agent`)
- [ ] 2.5 Kumpulkan outcome forward realized untuk kedua jalur — target minimum ~20-30 trade closed per jalur sebelum kesimpulan dianggap valid (mengacu ambang `historical_scorer` sendiri yang butuh ≥10/ticker)
- [ ] 2.6 *(Bisa paralel dengan 2.1-2.5)* Tinjau ulang bobot 5-metode fair value untuk horizon 3-15 hari — uji dampak menurunkan/menonaktifkan bobot DDM/DCF terhadap funnel dan verdict akhir

---

## Fase 3 — Keputusan Berbasis Data (setelah Fase 2 menghasilkan volume cukup)

*Estimasi: 1 sesi analisis, tapi hanya bisa dimulai setelah Fase 2.5 tercapai — jangan dipaksakan lebih cepat.*

- [ ] 3.1 Analisis komparatif: apakah performa forward single-agent+governor setara atau lebih baik dari multi-agent debate pada sampel yang terkumpul?
- [ ] 3.2 Jika setara/lebih baik → deprecate atau sederhanakan `debate_chamber.py` (opsi: pangkas jadi maksimal 1 ronde, atau pensiunkan penuh demi single-agent+governor)
- [ ] 3.3 Jika multi-agent terbukti lebih baik → dokumentasikan bukti nilai tambahnya (yang sebelumnya belum terbukti) dan tutup temuan Dimensi 2/4 audit ini sebagai closed
- [ ] 3.4 Putuskan nasib metode fair value berdasarkan data 2.6 (pangkas metode berbobot rendah, atau pertahankan dengan justifikasi baru)

---

## Fase 4 — Pantau Berkelanjutan (tidak butuh aksi sekarang, tapi butuh trigger review)

*Tidak dijadwalkan — ini daftar kondisi pemicu, bukan tugas dengan tenggat.*

- [ ] 4.1 `core/historical_scorer.py` — aktifkan kembali reviewnya begitu realized outcome per ticker tembus ambang ≥10 (saat ini sistem baru punya 23 closed total, jauh di bawah ambang per-ticker)
- [ ] 4.2 Tiga-lapis regime (`regime.py`/`regime_hmm.py`/`regime_gate.py`) — jadwalkan review konsolidasi non-urgent (dua classifier menjawab pertanyaan stres pasar yang sama)
- [ ] 4.3 Pertahankan dan jadwalkan ulang budaya audit-diri tim (ablation study, validasi IC sinyal) secara berkala — mis. tiap kuartal atau tiap kali regime pasar berubah signifikan

---

**Referensi silang:** temuan detail dan bukti tiap item ada di `docs/research/over_engineering_audit_2026-07-06.md`. Update memory `project_over_engineering_audit.md` jika ada keputusan besar yang diambil dari checklist ini (terutama hasil Fase 3).
