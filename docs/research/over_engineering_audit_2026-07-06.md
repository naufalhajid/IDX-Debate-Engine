# Audit 360° Over-Engineering — Sistem Swing Trading IDX

**Tanggal:** 2026-07-06
**Metode:** Pembacaan kode langsung (bukan sekadar dokumentasi) + 3 Explore agent paralel (2 selesai, 1 gagal mid-stream lalu diselesaikan manual via grep) + verifikasi empiris langsung (`idx filter`, `idx debate ERAA`) + penggalian data historis (backtest_memory, debate archive, laporan riset internal)
**Cakupan kode:** ~44.449 baris produksi (`core/`+`services/`+`utils/`+`app/`), ~19.401 baris test (72 file), 245 commit total
**Pertanyaan inti:** Bisakah sistem ini memilih saham swing trade IDX sama baiknya dengan kompleksitas yang jauh lebih rendah?

---

## Catatan metodologis penting

Proyek ini punya budaya audit-diri yang sudah cukup matang — enam laporan riset internal di `docs/research/` (ablation study, validasi IC sinyal screener, diagnostik gate re-open, log rekalibrasi fundamental, kalibrasi sentimen, riset forecasting) sudah menjawab sebagian besar pertanyaan yang diminta audit ini, dan dengan rigor yang lebih tinggi dari kebanyakan proyek pada tahap ini. Audit ini memverifikasi ulang temuan-temuan itu secara independen (baca kode langsung, jalankan ulang sebagian), bukan mengutip ulang begitu saja — di beberapa titik saya secara eksplisit mengonfirmasi via jalur berbeda (mis. `single_agent_analyzer.py` dibaca langsung untuk mengonfirmasi klaim ablation study, dan `idx debate ERAA` dijalankan ulang 2 hari setelah ablation study untuk melihat apakah polanya bertahan).

---

## DIMENSI 1 — KEBUTUHAN KOMPONEN

### 1A/1C — Inventaris Komponen & Deteksi Redundansi

| Komponen | Fungsi | Nilai tambah pemilihan saham | Jika dihapus | Verdict |
|---|---|---|---|---|
| `core/regime.py` | Vol realized 20 hari + aturan MA → label regime + override parameter scoring | Menyuplai parameter defensif ke banyak konsumen (quant_filter, technicals, debate seed) | Banyak titik lain kehilangan sumber label regime default | ✅ Esensial (tapi overlap parsial dgn #2, lihat 1C) |
| `core/regime_hmm.py` | HMM Gaussian 5-fitur → BULL/SIDEWAYS/BEAR_STRESS + override MSCI (aktif s.d. Nov 2026) | Live-terbukti: run ERAA hari ini membaca BEAR_STRESS confidence 99.8% dan memicu peringatan halt | Gate keras regime hilang | ✅ Esensial |
| `core/regime_gate.py` | Node LangGraph yang mengeksekusi keputusan trade/halt dari `regime_hmm` | Titik keputusan tunggal yang benar-benar dieksekusi graph | Graph tidak punya gate regime sama sekali | ✅ Esensial |
| `core/quant_filter/` (pipeline+config, ~2.700 baris) | Screener deterministik: 15+ sinyal → funnel 957→803→445→~1-2 | Live hari ini: 3 gate (likuiditas ADT, trend EMA20, RS vs IHSG) = 89% dari seluruh penolakan | Funnel jadi lebih longgar tapi 3 gate dominan bisa berdiri sendiri | ⚠️ Marginal — mayoritas signal computed tapi hanya jadi kolom tampilan, bukan input scoring (lihat 3B) |
| `services/debate_chamber.py` (5.361 baris) | LangGraph 12-node: 3 scout paralel → debat bull/bear s.d. 3 ronde → CIO judge | Lihat Dimensi 2 — nilai tambah terukur di atas gate deterministik BELUM terbukti | Sistem kembali ke single-agent + gate (ablation menunjukkan hasil terstruktur mirip, biaya jauh lebih rendah) | ⚠️ Marginal |
| `services/single_agent_analyzer.py` | Baseline 1-panggilan-LLM untuk perbandingan akademis | Confirmed langsung: TIDAK PERNAH memanggil `risk_governor` atau validasi envelope apa pun | N/A — ini alat pembanding, bukan jalur produksi utama | ✅ Esensial (sebagai instrumen ablation) |
| `core/risk_governor.py` (1.078 baris) | Gate deterministik: floor R/R tier-aware, likuiditas ADT, ex-date, ARA/ARB, counter-trend | **Terbukti 2x independen** (ablation 25-ticker + live run ERAA saya sendiri) sebagai mekanisme yang benar-benar menentukan verdict akhir | Sistem kehilangan satu-satunya lapisan yang terbukti menghasilkan edge nyata | ✅ Esensial — paling terbukti di seluruh sistem |
| `services/fair_value_calculator.py` (2.022 baris) | 5 metode valuasi (PE/PB/DDM/EV-EBITDA/DCF) + dispersion-gate + staleness-shift + diskon SOE + normalisasi siklikal | Tiap layer bisa ditelusuri ke bug spesifik (mis. FV-6 dibuat karena ICBP/UNVR pernah overstate +135%/+240%) — disiplin nyata | Fair value jadi kasar, tapi CIO tetap punya gate R/R independen dari fair value | ⚠️ Marginal — banyak mesin untuk satu angka yang bahkan dibuang jika tidak ada sitasi RAG (lihat live run ERAA: klaim gap +253%, `risk_overvalued=False`, tapi tidak pernah diverifikasi silang apakah klaim seagresif itu wajar) |
| `core/portfolio_optimizer.py` vs `core/portfolio_guard.py` | Cap sektor+cluster-korelasi di titik seleksi vs guard heat/drawdown di titik entry baru | Confirmed via agent: **tidak overlap** — tanggung jawab bersih terpisah, fitur V4.3/V4.4 mendarat di file yang tepat tanpa duplikasi | Kehilangan diversifikasi ATAU kehilangan proteksi entry-baru, dua risiko berbeda | ✅ Esensial, keduanya |
| `core/forecasting/` (6 model file) | Ensemble Naive+XGBoost+TGARCH (aktif); ARIMA/LSTM/Prophet | Confirmed via agent: ARIMA/LSTM/Prophet **tidak pernah diinstansiasi** — `service.py` memalsukan placeholder vote `weight=0.0` untuk LSTM/Prophet, ARIMA orphaned total | Nol dampak — 3 dari 6 file sudah mati fungsional | ❌ Redundant (untuk 3 file mati) / ⚠️ Marginal (untuk 3 aktif — lihat 3A) |
| `core/historical_scorer.py` + `core/backtest_memory.py` | Penyesuaian conviction ±0.05 dari win-rate historis realized | Butuh ≥10 outcome closed per ticker; **total sistem hanya 59 record, 23 closed** — hampir pasti tidak pernah aktif untuk ticker manapun hari ini | Tidak ada — sudah efektif no-op | ⚠️ Marginal — mesin FinMem-lite yang lapar data, saat ini dorman |
| Cluster meta-infra (16 file, `core/`+`services/`) | Ledger eksekusi, taksonomi kegagalan, handoff envelope, observation store, health provider, dsb. | **13/16 confirmed live-substantif** di `legacy.py`/`debate_chamber.py` (bukan sekadar diimpor) — mengoreksi hipotesis awal saya bahwa ini kemungkinan besar ceremony | Reliabilitas pipeline LLM kompleks ini akan langsung rapuh | ✅ Esensial-sebagai-plumbing (tapi tidak menyentuh kualitas seleksi saham secara langsung — lihat 1B) |
| ↳ 3 file mati dari cluster di atas: `verification_runner.py`, `agent_eval_harness.py`, `tool_registry.py` | Dev-tool checker domain / golden-case harness / registry tool typed | **Confirmed: hanya direferensikan test filenya sendiri**, nol pemanggil produksi | Nol dampak | ❌ Redundant — aman dihapus |
| `utils/technicals.py` (14 fungsi indikator) | RSI/ATR/MACD/Bollinger/VWAP/Fibonacci/flag-pattern/volume-profile/dst. | Sebagian besar (MACD histogram, pola candlestick, posisi Bollinger, divergensi RSI, gap, kompresi) computed di `quant_filter/pipeline.py` HANYA jadi field tampilan — tidak masuk `total_score` | Scoring screener tidak berubah sama sekali | ⚠️ Marginal — GAP-09 laporan lama sudah menandai Fibonacci spesifik; pola yang sama berlaku ke beberapa sibling-nya |

### 1B — Uji "Apakah Ini Akan Berpengaruh"

Tiga bukti konkret, bukan hipotetis:

1. **Ablation V2.1** (internal, 25 ticker, 2026-07-04): dari 12 ticker dengan verdict single-agent vs multi-agent yang berbeda, **12/12 (100%)** dijelaskan oleh kode gate deterministik (`rr_too_low`, `no_momentum_confirmation`, dst di `risk_governor.py`/envelope), nol oleh "penalaran LLM yang lebih dalam." `single_agent_analyzer.py` tidak pernah memanggil gate yang sama — jadi BUY-nya tidak pernah punya kesempatan ditolak.
2. **Live run ERAA saya sendiri** (2026-07-06, independen dari ablation): 3 ronde debat penuh, 5 agen memberi suara campuran (Bull BUY 56%, Bear AVOID 62%, tiga lainnya HOLD), tapi *Decision Summary* menyatakan eksplisit: **"Final decision HOLD: Setup ditolak: rr_too_low: R/R 0.56 < 1.4."** Argumen kualitatif Bull-Bear soal level support Rp 331/357/378 — yang menjadi keunggulan naratif dari desain debat — sama sekali bukan yang menentukan hasil akhir.
3. **Funnel screener hari ini**: dari 445 ticker lolos filter statis, hanya 2 lolos scoring teknikal. Lima belas-plus sinyal komposit yang dirancang untuk *meranking* kandidat pada praktiknya beroperasi pada populasi 0-2 nama di hari yang khas — kekuatan diskriminatifnya jarang benar-benar teruji.

Kesimpulan 1B: komponen yang **jika dihapus benar-benar mengubah rekomendasi** adalah risk_governor, gate likuiditas/trend/RS, dan regime_hmm halt. Komponen yang **jika dihapus nyaris tidak mengubah keluaran** meliputi 3 file forecasting mati, 3 file meta-infra mati, historical_scorer (dorman), dan mayoritas indikator teknikal display-only.

---

## DIMENSI 2 — KOMPLEKSITAS ARSITEKTUR AGENT

### 2A — Justifikasi Jumlah Agent

Graph `debate_chamber.py` (dikonfirmasi via `add_node`/`add_edge`): 12 node — `regime_gate → scout_dispatcher → [fundamental, chartist, sentiment] → synthesizer → bullish_analyst → bearish_auditor → consensus_evaluator → (devils_advocate | state_cleaner→bullish_analyst) → devils_advocate → cio_judge`.

Setiap agent secara nominal membuat keputusan berbeda (fundamental/teknikal/sentimen/bull/bear/konsensus/stress-test/final), dan mekanisme voting (5 agen, threshold 80% ronde-1 lalu 60%, aturan `soft_hold` untuk selisih tipis bull-bear, aturan `deadlock_hold` untuk kebuntuan BUY-vs-AVOID, bobot kalibrasi per-agen yang mendiskon Bear ke 0.85 agar tidak otomatis menang tiebreak) semuanya **bisa ditelusuri ke insiden spesifik**, bukan desain spekulatif. Ini bukan "teater" dalam arti sembarangan.

Tapi pertanyaan kuncinya — **apakah satu langkah analisis yang dirancang baik akan menghasilkan pilihan saham yang sama baiknya?** — dijawab evidence di 1B: struktur yang benar-benar menentukan hasil (gate R/R, envelope) sudah bisa dijalankan langsung di atas output single-agent tanpa 3 ronde debat.

### 2B — Overhead Orkestrasi & 2C — LLM vs Deterministik

- **Panggilan LLM per ticker**: baseline single-agent = 1 panggilan flash (~20 detik). Multi-agent debate = rata-rata **~10 panggilan flash + ~1 panggilan pro** (dihitung dari ablation: 178 flash + 1 pro / 25 ticker untuk leg debat saja, ditambah 3 scout). Run ERAA saya butuh **6 menit 42 detik** untuk SATU ticker.
- **Reliabilitas**: ablation mencatat kegagalan koneksi transient 16% (4/25 ticker) di leg multi-agent vs 0% di single-agent pada sampel yang sama.
- **Plumbing LLM itu sendiri rapi**: satu jalur `_invoke_llm_for_state → _invoke_llm_with_retry → _invoke_llm_attempt` dipakai SEMUA node (retry, budget accounting, klasifikasi tier, injeksi global-rules) — bukan duplikasi 8x, ini abstraksi yang baik.
- **LLM dipakai untuk threshold sederhana?** Tidak ditemukan kasus mencolok "LLM menilai RSI oversold" — perhitungan RSI/ATR/MACD semua deterministik di Python. Yang LLM lakukan adalah sintesis naratif dan penilaian kualitatif (sentimen, argumen bull/bear) — secara desain bukan hal yang trivial untuk deterministik. Kritiknya bukan "LLM dipakai untuk hal sepele," tapi "LLM dipakai untuk hal yang hasilnya kemudian dikalahkan gate deterministik yang lebih murah."

---

## DIMENSI 3 — KOMPLEKSITAS KALKULASI & MODEL

### 3A — Sofistikasi vs Payoff

- **Fair value** (5 metode): setiap penambahan (DCF via OCF, EV/EBITDA mining, dispersion-gate, staleness-shift, diskon SOE 15%, normalisasi EPS siklikal mining) punya jejak commit/bug spesifik — pola perbaikan reaktif yang disiplin, bukan penambahan spekulatif. Tapi untuk horizon 3-15 hari, model perpetuitas seperti DDM/DCF (dirancang untuk valuasi jangka panjang) bernilai dipertanyakan — GAP-10 lama sudah mengangkat ini, belum terjawab tuntas.
- **Forecasting**: tim SUDAH memangkas dari klaim 6-model ke 2 aktif (Naive sebagai anchor BH, XGBoost sebagai model utama) + TGARCH untuk volatilitas, persis mengikuti rekomendasi riset internal mereka sendiri (perbaikan simulasi TGARCH, penghapusan ARIMA, threshold IC t-stat diturunkan 2.57→1.96, filter directional-accuracy 0.45 ditambahkan — **kelimanya sudah diimplementasikan**, dikonfirmasi baca kode langsung). Ini contoh terbaik disiplin self-correction di seluruh sistem.
- **GARCH ATR dinamis**: pada run `idx filter` hari ini, puluhan warning "GARCH non-stationary" dan "capping ke 3× classic ATR" muncul — model per-ticker sering gagal konvergen dan terdegradasi jadi pengali tetap. **Saya tidak sempat memverifikasi apakah ini benar-benar pernah membalik keputusan gate** (R/R → floor) — sesuai kehati-hatian, ini harus dilaporkan sebagai "noise operasional + nilai belum terbukti," bukan "mengubah pilihan saham."

### 3B — Redundansi Sinyal

- Validasi IC internal (`screener_signal_ic_2026-07-02.md`, 12-19 periode — sampel kecil, hasil "belum tervalidasi" bukan "terbukti noise"): **0 dari 11 sinyal** lolos ambang HLZ+FDR. Tim merespons dengan memangkas bobot (`weight_momentum_vol` 8→0, `weight_momentum_rsi` 15→8) — bukan mengabaikan hasil. Disiplin nyata.
- Funnel screener hari ini: 3 gate (likuiditas, trend, RS) = 89% penolakan; belasan sinyal lain (Piotroski, Altman-Z, OCF/Price, RNOA, MACD, candlestick, Bollinger, dst.) bersaing memperebutkan sisa 1-2 nama.
- 14 fungsi indikator teknikal di `utils/technicals.py`; beberapa (VWAP, anchored VWAP, Fibonacci, flag pattern, volume profile) adalah alat analisis teknikal cukup eksotis untuk pasar yang literatur IDX-nya belum memvalidasinya (persis kekhawatiran GAP-09 lama).

### 3C — Kompleksitas Fair Value untuk Swing Trading

Live run ERAA hari ini menunjukkan **Valuation Gap +253.0% (UNDERVALUED)** — fair value Rp 1.292 vs harga Rp 366. Klaim undervaluation sebesar ini pada horizon 5-20 hari trading patut dicurigai terlepas dari mekanisme dispersion-gate yang sudah ada; sistem tidak menandainya sebagai `fair_value_rejected` di run ini. Untuk strategi teknikal jangka pendek, pertanyaan GAP-10 ("apakah valuasi fundamental dalam ini bahkan diperlukan?") masih relevan dan belum dijawab tuntas.

---

## DIMENSI 4 — RASIO KOMPLEKSITAS-KE-EDGE

### 4A — Uji Pikiran Sistem Minimal Viable

**MINIMAL VIABLE SWING SELECTOR** yang tersirat dari bukti hari ini: filter likuiditas (ADT) + filter trend (EMA20) + filter relative-strength vs IHSG + regime halt (HMM BEAR_STRESS) + floor R/R tier-aware. Lima komponen ini menjelaskan **89% dari penolakan screener** dan **100% dari divergensi verdict** yang teramati di ablation. Sistem saat ini menambahkan: multi-agent debate 12-node, 5-metode fair value + 4 layer koreksi, forecasting ensemble, historical scorer, 3 file regime, 2 file portfolio, cluster meta-infra 16-file, dan 14 fungsi indikator teknikal — di atas lima komponen minimal itu.

### 4B — Akuntansi Biaya Kompleksitas

- Titik kegagalan lebih banyak: setidaknya 3 file meta-infra mati + 3 file forecasting mati + 1 kontradiksi desain terdokumentasi-tapi-belum-diselesaikan (lihat di bawah).
- Latensi: 6m42s per ticker untuk debate penuh (vs ~20 detik single-agent).
- Biaya LLM: ~10-11x lipat panggilan per ticker.
- Risiko overfitting: 15+ sinyal discreener dengan 0/11 lolos validasi statistik pada sampel kecil.

### 4C — Titik Diminishing Returns: Kontradiksi Preflight vs Envelope

Ini temuan paling konkret untuk Dimensi 4. Laporan diagnostik internal (`diagnostic_gate_reopen_2026-07-02.md`) menemukan dan **mendokumentasikan secara eksplisit** bahwa dua gate berbeda memakai definisi "stop" yang tidak konsisten: preflight pakai `price − low_20d` vs floor `1.0×ATR`, sedangkan envelope resmi (yang dipakai `risk_governor`) pakai `entry_high − stop` dengan stop di bawah swing-low. Akibatnya: saham yang *sengaja* diloloskan envelope untuk setup mean-reversion (RSI < 40, dekat low 20 hari) hampir pasti ditolak preflight karena — secara struktural — dekat dengan low 20 hari berarti `price − low_20d` kecil. Tim sendiri menulis "Tension desain" dan mencantumkannya sebagai tindak lanjut V1.x yang **belum diputuskan**.

Ini bukti langsung bahwa pola akresi sistem ini adalah: **setiap patch lokal rasional dan terdokumentasi baik (masing-masing gate individual masuk akal), tapi tidak ada proses konsolidasi global** yang memastikan gate-gate baru konsisten dengan gate lama. Disiplin per-insiden yang tinggi justru menjadi mekanisme akresi kompleksitas itu sendiri, bukan penangkalnya.

---

## DIMENSI 5 — VERIFIKASI EMPIRIS

### 5A/5B — Eksekusi Langsung & Kontribusi Komponen

**`uv run idx filter --top 10`** (dijalankan hari ini, gratis, deterministik): regime DEFENSIVE, funnel 957→803→445→2→1 (skor floor), kandidat tunggal **ERAA** (skor 65,2, harga Rp 366, Graham FV Rp 994, upside klaim +177,6%). Sama persis dengan satu-satunya kandidat di ablation study 2 hari sebelumnya — funnel konsisten dan reproducible.

**`uv run idx debate ERAA`** (dijalankan hari ini, live, biaya nyata): HMM BEAR_STRESS confidence 99,8%; IndoBERT sentiment model berhasil dimuat (mengonfirmasi integrasi live per catatan proyek sebelumnya); 3 ronde debat, 5 suara agen campuran (BUY/AVOID/HOLD×3); **verdict akhir HOLD 40%, ditentukan oleh `rr_too_low: R/R 0.56 < 1.4`** — gate deterministik yang sama seperti di ablation study, dikonfirmasi independen. Durasi 6m42s.

### 5C/5D — Simulasi Simplifikasi & Realita Biaya

**Koreksi penting atas klaim yang terlalu jauh**: godaan untuk menyimpulkan bahwa hasil ERAA "tidak akan berubah" di bawah single-agent+risk_governor harus ditahan. Jalur multi-agent memang memberi CIO sebuah Trade Envelope yang dihitung Python (entry/target/stop) dan secara eksplisit melarangnya "menciptakan" harga sendiri — tapi `single_agent_analyzer.py` (dikonfirmasi dari skema JSON prompt-nya: `"target_price": <float>, "stop_loss": <float>, "risk_reward_ratio": <float>`) meminta LLM **menghasilkan sendiri** ketiga angka itu. Artinya jalur single-agent akan memberi `risk_governor._recompute_rr` harga target/stop buatan-LLM yang berbeda dari envelope Python 0,56 di atas — R/R-nya tidak otomatis terulang begitu saja. Ini justru alasan tepat kenapa **eksperimen paralel** (bukan inferensi dari kesamaan struktural semata) diperlukan: kita secara jujur tidak bisa menyimpulkan hasilnya tanpa benar-benar menjalankannya. Yang SUDAH terbukti sama persis di 12/12 kasus ablation adalah *mekanisme yang menjelaskan divergensi* (gate deterministik, bukan kedalaman LLM) — bukan klaim bahwa angka R/R spesifiknya akan identik lintas jalur.

**Data hasil realized (dari `core/backtest_memory.py`, live query hari ini):**

```
Total record       : 59
Wins                : 2
Losses              : 21
Open (belum resolve): 36
Win rate (closed)   : 8,7%  (2/23)
Avg P/L             : -1,61%
Avg confidence entry: 61,7%
```

Indikator paling tajam di sini **bukan** win rate 8,7% mentah (itu terjadi berbarengan dengan crash Juni 2026 — sistem minimal pun kemungkinan besar juga rugi di periode itu, jadi menyalahkan kompleksitas secara spesifik atas kerugian ini terlalu jauh). Yang lebih tajam adalah **jarak antara confidence rata-rata (61,7%) dan win rate realized (8,7%)** — LLM secara sistematis overconfident relatif terhadap hasil nyata, pada n=23 closed trade (kecil, catatan kehati-hatian tetap berlaku). Ini kritik langsung ke kualitas output lapisan LLM, bukan sekadar "pasar sedang jelek."

Ini bukan sekadar perbandingan longgar "confidence terasa tinggi vs hasil buruk" — `core/quant_filter/position_sizer.py:compute_kelly_fraction()` secara eksplisit memakai confidence CIO SEBAGAI `win_prob` dalam rumus Kelly (`f* = (p×b−q)/b`), dengan komentar kode sendiri: *"p = win_prob (CIO confidence as proxy)"*. Sistem ini sendiri — bukan interpretasi audit — memperlakukan confidence sebagai estimasi P(menang) untuk menentukan besar posisi. P(menang) aktualnya 8,7%. Ini bukan kesalahan kategori dari sisi audit, melainkan miskalibrasi yang terbukti dari definisi sistem itu sendiri.

Fakta pendukung lain: dari **846 debat historis** yang pernah dijalankan, hanya **59 (7%)** yang pernah dicatat outcome-nya — feedback loop yang seharusnya memberi makan `historical_scorer` nyaris tidak pernah terisi, menjelaskan kenapa mekanisme itu dorman (lihat 1A).

---

## SINTESIS AKHIR — VERDICT OVER-ENGINEERING

```
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SKOR DIMENSI (semakin tinggi = semakin proporsional):

  1. Kebutuhan Komponen        : 6/10
  2. Arsitektur Agent           : 5/10
  3. Kompleksitas Kalkulasi     : 6/10
  4. Rasio Kompleksitas-ke-Edge : 5/10
  5. Justifikasi Empiris        : 7/10
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TOTAL: 29/50

VERDICT: 🟡 MILDLY OVER-ENGINEERED (28-39/50)

Inti sistem sudah tepat sasaran; kompleksitas tidak
menyebar merata sebagai kebusukan — ia terkonsentrasi
di lokasi yang bisa diidentifikasi dengan jelas.
→ Aksi: Simplifikasi tertarget, bukan redesain total.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

**Komponen paling over-engineered:** Lapisan debat multi-agent (`debate_chamber.py`) relatif terhadap nilai tambahnya yang belum terbukti — ~10x panggilan LLM, kegagalan transient 16%, latensi 6-7 menit/ticker, untuk verdict yang **dua kali dikonfirmasi independen** (ablation + live run saya sendiri) ditentukan oleh gate deterministik yang sama yang bisa dijalankan langsung di atas output single-agent.

**Kompleksitas paling terjustifikasi:** `core/risk_governor.py` + gate screener (likuiditas/trend/RS). Terbukti dua kali sebagai mekanisme yang benar-benar mengubah keputusan, murah secara komputasi, dan tiap ambangnya bisa ditelusuri ke regulasi BEI atau kegagalan nyata yang teramati.

**Jawaban pertanyaan inti — "Bisakah sistem ini memilih saham sama baiknya dengan kompleksitas jauh lebih rendah?"**
**SEBAGIAN (PARTIALLY).** Untuk irisan besar sistem — screener deterministik + risk governor + regime halt — bukti struktural kuat menunjukkan versi yang jauh lebih sederhana akan menghasilkan pilihan yang nyaris identik, dengan biaya jauh lebih rendah. Tapi ini belum menjadi bukti performa forward yang tervalidasi (baru kesamaan struktural + akuntansi biaya, persis seperti yang dicatat laporan ablation internal sendiri). Untuk bagian lain — regime HMM, portfolio guard/optimizer, gate ex-date/ARA/ARB — kompleksitas sudah terbukti perlu dan tidak berlebihan.

---

### ROADMAP SIMPLIFIKASI

**🔴 Hapus/gabung (aman — kode mati terkonfirmasi, nol nilai hilang):**
1. `core/verification_runner.py`, `core/agent_eval_harness.py`, `core/tool_registry.py` — confirmed nol pemanggil produksi, hanya dirujuk test filenya sendiri.
2. `core/forecasting/models/lstm.py`, `models/prophet_model.py` — confirmed tidak pernah diinstansiasi; `service.py` memalsukan placeholder vote, bukan memanggilnya.
3. `core/forecasting/models/arima.py` — orphaned sejak dikeluarkan dari ensemble; tidak dirujuk kode produksi manapun.
4. Audit ulang field display-only di `quant_filter/pipeline.py` (MACD histogram, pola candlestick, posisi Bollinger, divergensi RSI, gap, kompresi volatilitas) — jika prompt chartist tidak benar-benar memakainya secara bermakna, hentikan komputasinya.

**🟠 Simplifikasi (nilai sama, kompleksitas lebih rendah — AKTIF tapi BELUM TERBUKTI, butuh eksperimen paralel murah, bukan penghapusan buta):**
1. **Multi-agent debate vs single-agent+risk_governor** — pasang `risk_governor` + validasi envelope langsung di atas output `single_agent_analyzer.py`, jalankan keduanya paralel pada sampel ticker yang sama, dan biarkan **outcome forward realized** (bukan kesepakatan struktural) yang memutuskan apakah 10x biaya debat sepadan.
2. **Fair value 5-metode untuk horizon 3-15 hari** — tinjau ulang apakah DDM/DCF (model perpetuitas) menambah nilai dibanding PE/PB/EV-EBITDA untuk holding period sesingkat ini, mengingat lapisan verifikasi-sitasi-RAG sudah membuang FV yang tidak terverifikasi.
3. **GARCH ATR dinamis** — konfirmasi dulu apakah ia pernah benar-benar membalik hasil gate (stop→R/R→floor) sebelum mempertahankannya sebagai default; jika tidak, kembali ke ATR klasik yang deterministik dan tidak pernah non-stationary.

**🟡 Simpan tapi pantau:**
1. `historical_scorer.py`/`backtest_memory.py` — sudah ter-guard dengan baik (ambang minimum 10 record) sehingga aman sebagai no-op saat ini; tinjau ulang begitu volume outcome realized melewati ambang aktivasinya sendiri.
2. Tiga-lapis regime (`regime.py`/`regime_hmm.py`/`regime_gate.py`) — tidak redundan secara efek (beda layer), tapi "dua classifier sama-sama menjawab apakah pasar sedang stres" layak ditinjau untuk konsolidasi di masa depan.
3. Gap-risk stress metric di `position_sizer.py` (V4.4) — oleh penulisnya sendiri ditandai "informational only... pending review of the magnitude this produces". Putuskan perannya sebelum ia diam-diam jadi load-bearing.

**Yang harus dipertahankan (kompleksitas yang genuinely terjustifikasi):**
1. `core/risk_governor.py` — locus edge yang paling terbukti di seluruh sistem.
2. Tiga gate dominan screener (likuiditas ADT, trend EMA20, RS vs IHSG) — 89% penolakan hari ini.
3. Budaya audit-diri empiris tim sendiri (ablation study, validasi IC, log rekalibrasi) — inilah mekanisme yang justru akan menjawab tuntas pertanyaan Dimensi 4 jika terus dijalankan.

---

### ASESMEN JUJUR AKHIR

Sistem ini over-engineered secara ringan-ke-moderat, dan terkonsentrasi di satu lokasi spesifik: lapisan debat multi-agent LLM menelan sekitar 10x panggilan LLM dari analisis single-agent (dan gagal transient 16% dari waktu) untuk mencapai verdict yang — baik oleh ablation study internal maupun run langsung independen saya sendiri — ternyata ditentukan oleh gate deterministik `risk_governor` yang sama, yang juga bisa diterapkan pada desain jauh lebih murah. Lapisan deterministik itu sendiri — deteksi regime, floor R/R, gate likuiditas/ex-date/ARA-ARB — bukan hasil over-engineering; tiap ambangnya berjejak ke regulasi BEI atau kegagalan nyata yang pernah teramati, dan dua kali terbukti menjadi mekanisme yang benar-benar mengubah keputusan. Sinyal paling mengkhawatirkan bukan satu komponen tunggal yang bengkak, melainkan pola akresinya: 245 commit berisi patch yang disiplin dan individually terjustifikasi (pemangkasan bobot berbasis IC, penghapusan ARIMA, perbaikan dispersion fair value) tetap menghasilkan dua file di atas 5.000 baris dan satu kontradiksi desain (preflight vs envelope) yang didokumentasikan tim sendiri sebagai belum terselesaikan — bukti bahwa disiplin lokal tidak otomatis menghasilkan koherensi global. Rekam jejak realized (2 menang, 21 kalah, confidence rata-rata 61,7% vs win rate 8,7%) adalah indikasi paling tajam yang tersedia, meski crash Juni menjadi confounder untuk menuduh kompleksitas sebagai penyebab spesifik kerugian tersebut. Langkah tunggal paling bernilai bukan menghapus sesuatu yang dramatis — tiga file mati dan dua model forecasting yang tidak terpakai adalah pemangkasan murah dan aman — melainkan menjalankan eksperimen murah yang sudah ditunjuk oleh ablation study tim sendiri: pasangkan risk governor ke output single-agent, dan biarkan hasil forward realized — bukan kesepakatan struktural — yang memutuskan apakah tambahan 10x biaya debat LLM ini pantas dipertahankan.
