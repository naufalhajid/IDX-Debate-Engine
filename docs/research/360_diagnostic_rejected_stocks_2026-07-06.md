## Diagnosis 360° — "Kenapa Saham yang Ditolak Malah Naik, Apakah Sistem Gagal?"

- Tanggal: 2026-07-06
- Pemicu: BREN/BRPT/PTRO/DSSA naik 30–50% dalam beberapa minggu (bahkan saat IHSG turun),
  sementara sistem menolaknya dan "tidak menghasilkan output yang bisa dieksekusi".
- Metode: **dijalankan, bukan diasumsikan.** Semua angka di bawah berasal dari run
  nyata + artefak run hari ini, bukan tebakan.

---

### TL;DR — Vonis

> **Sistem TIDAK rusak dan TIDAK salah kalibrasi parah. Menolak BREN/BRPT/PTRO/DSSA
> adalah DISIPLIN, bukan kegagalan.** BREN diperdagangkan **13.5× fair value** dengan
> **free float 5%**; itu profil gorengan/spekulatif klasik, bukan swing setup sehat.
>
> **"Tidak ada output" itu NYATA, tapi penyebabnya bukan filter fundamental yang
> kelewat ketat.** Penyebabnya: di tape pasca-crash yang bergejolak ini, **irisan
> antara "sedang naik" ∩ "tidak overvalued" ∩ "R/R ≥ 1.4" memang kosong** — saham
> bagus sedang turun (falling knife), saham yang naik overvalued. Sistem sudah punya
> mekanisme untuk kedua bahaya itu, dan sekarang keduanya menyala bersamaan.
>
> **Masalah sesungguhnya bukan yang dikhawatirkan user.** Track record realized sistem
> **2 menang / 21 kalah**. Dua kemenangan itu justru saham momentum kecil (NZIA +33%,
> MBSS +20%). 21 kekalahan hampir semuanya **BUY berulang ke BMRI & DMAS saat crash Juni
> → kena stop**. "0 BUY" sekarang adalah **koreksi** atas keagresifan itu, bukan bug baru.
>
> **Yang benar-benar hilang:** cara MENGUKUR apakah sistem punya edge sama sekali. 23
> trade (mayoritas di satu crash) bukan sampel. Itu P0 sebenarnya — bukan melonggarkan
> threshold biar nangkap BREN.

**Diagnosis utama: KOMBINASI —**
- ✅ **Bekerja benar tapi konservatif** untuk saham spekulatif (BREN/DSSA/CUAN/BUMI/CDIA).
- 🟠 **Mismatch strategi-vs-tape**: sistem trend-following long tidak bisa menyala di
  pasar turun; mode mean-reversion pun kosong hari ini (terverifikasi).
- 🟠 **Ketidakmampuan mengukur edge**: 2W/21L bukan basis untuk keputusan apa pun.

---

### Basis bukti (apa yang benar-benar dijalankan)

| Sumber | Isi |
|---|---|
| `output/latest_batch_report.md` (run 20260706_171323, **hari ini**) | 25 saham didebatkan → **0 BUY**, 23 HOLD, Executable: **None** |
| `output/debates/*/latest_debate.json` (hari ini) | verdict + reason code per saham (tabel di bawah) |
| `idx filter mr` (dijalankan hari ini ~19:57, regime DEFENSIVE) | 957 → 445 lolos fundamental → **0** lolos teknikal |
| Diagnostic momentum_floor_bypass (hari ini 16:06, regime HIGH) | 957 → **1** lolos teknikal (MAPI) |
| `docs/research/diagnostic_gate_reopen_2026-07-02.md` | override regime→NORMAL **tetap** 0 BUY |
| `output/backtest/backtest_memory.jsonl` | track record realized **2W / 21L** |

---

### Dimensi 1 — Masalah "No Output"

**Funnel nyata hari ini (regime DEFENSIVE, `idx filter mr`):**

```
Universe (XLSX)            :  957
Setelah exclude PEMANTAUAN :  803
Setelah static filter      :  445   (DER, PBV, harga)  <- fundamental LOLOS di sini
Setelah technical scoring  :    0   (EMA, RSI, likuiditas, volume)  <- RUNTUH DI SINI
Setelah score floor        :    0
Final output               :    0
Watchlist pre-floor        :  MAPI (75.5), BFIN (33.0)
```

**Titik runtuh: tahap technical scoring, bukan fundamental.** 445 saham lolos gate
fundamental (DER/PBV/harga). Yang menyingkirkan mereka adalah baterai teknikal:
likuiditas (ADT), tren (EMA20), relative strength vs IHSG, ATR%. Gate teknikal itu
**sah untuk swing trading** — trend + likuiditas + relative strength memang kriteria
inti. Jadi funnel runtuh bukan karena "kelewat fundamental", tapi karena **di tape ini
nyaris tidak ada saham dengan setup teknikal swing yang valid**.

**Tapi 25 saham panas DILEWATKAN paksa ke debate — dan tetap 0 BUY.** User jelas sudah
mem-*bypass* filter dengan menjalankan 25 saham panas langsung ke pipeline. Hasilnya
(run hari ini): **semua HOLD, confidence 0.40 seragam, R/R null, Executable None.** Jadi
"no output" terjadi juga di lapis CIO/risk-governor.

**Akar penyebab reason code (bukan bug governor):** `core/risk_governor.py:174–199` —
kalau CIO memvonis **HOLD**, ia **tidak** memancarkan entry/target/stop → governor
otomatis menandai `invalid_entry_range` + `missing_target_price` + `rr_too_low`. **Reason
code itu GEJALA dari HOLD, bukan bug governor.** Governor jujur melaporkan "CIO tak
memberi setup tradeable". Pertanyaan naik ke CIO: kenapa HOLD? → `no_momentum_confirmation`
dan `rr_too_low` (Dimensi 3).

**Severity: 🟠 High** (nyata, tapi sebagian besar perilaku konservatif yang disengaja,
bukan kerusakan).

---

### Dimensi 2 — Forensik Saham yang Ditolak (BREN/BRPT/PTRO/DSSA)

Dari verdict CIO **nyata hari ini** (`output/debates/<T>/latest_debate.json`):

| Saham | Harga | FV (high) | Harga/FV | Free float | Verdict | Reason code utama | Tolakan benar? |
|---|---|---|---|---|---|---|---|
| **BREN** | 3.440 | 255 | **13.5x** | **5% (HIGH)** | HOLD | `EXTREME_OVERVALUATION`, `rr_too_low` | YA (benar) |
| **DSSA** | (feed anomali¹) | — | ~2.6x¹ | **6% (HIGH)** | HOLD | `EXTREME_OVERVALUATION`, `no_momentum_confirmation` | YA (benar) |
| **BRPT** | 1.505 | 1.410 | **1.1x** (~wajar) | 29% (LOW) | HOLD | `stop_inside_noise`, `invalid_entry_range` | BORDERLINE (geometri) |
| **PTRO** | 3.950 | — | — | 28% (LOW) | HOLD | `rr_too_low`, `fair_value_quality_reject` | BORDERLINE (R/R) |

Saham panas lain di batch yang sama, semuanya `EXTREME_OVERVALUATION`:
CUAN **1.8x**, BUMI **2.3x**, CDIA **3.0x**, BNBR **2.6x**, ASPR **3.0x**, RAJA **2.3x**.

**Verdict Dimensi 2: MIXED, condong BENAR.**
- **BREN & DSSA (dan CUAN/BUMI/CDIA): ditolak dengan benar.** 2–13× fair value + free
  float 5–6% = persis profil yang harus dihindari sistem disiplin. Ini bukan "sistem
  ketinggalan momentum"; ini "sistem menolak mengejar move yang sudah lari & detached
  dari fundamental". `momentum_play` (jalur beli-walau-fundamental-gagal) **tidak menyala
  satu pun** — pemicunya butuh volume breakout + technical PASS yang tak terkonfirmasi ulang.
- **BRPT & PTRO: lebih debatable.** Ditolak bukan karena overvalued (BRPT nyaris di fair
  value) tapi karena **geometri setup** (`stop_inside_noise`, `rr_too_low`). Ini area di
  mana sistem *mungkin* terlalu ketat — tapi lihat Dimensi 4 sebelum melonggarkannya.

**Insight "sudah lari":** stok yang sudah naik 30–50% hampir pasti RSI > 70 (hard-reject),
ATR% tinggi (gate 5% kena), dan `ara_entry_risk HIGH`. Menolaknya = menolak masuk di
puncak distribusi. Entry aman ada **lebih awal**; mengejar move extended adalah cara
trader meledak. Bahwa BREN naik 50% setelah ditolak **bukan bukti kegagalan** — itu
bukti disiplin.

¹ Feed harga DSSA di debate (`px=815`) tampak anomali vs harga pasar sebenarnya
(puluhan ribu Rp; kemungkinan efek stock-split/penyesuaian). Multiple pastinya perlu
diverifikasi ulang, tetapi **free float 6% + flag EXTREME_OVERVALUATION** berdiri
independen dari angka itu. → catat sebagai item data-quality (Dimensi 5).

---

### Dimensi 3 — Kecocokan Strategi vs Pasar

**Ini pasar seperti apa?** IHSG turun, tapi segelintir saham (grup Prajogo/Bakrie)
melonjak. Itu ciri pasar **sempit, rotasional, spekulatif** — **bukan** bull market luas.
Regime terdeteksi DEFENSIVE/HIGH (bergeser dalam sehari).

**Mekanisme "0 BUY" (dari BBRI — batu Rosetta),** `output/debates/BBRI/latest_debate.json`:
```
rating: HOLD | current_price 2.790 | fair_value 4.372  -> UNDERVALUED (0.64x)
summary: "Setup ditolak: no_momentum_confirmation: return_5d -1.8% < 0 at RSI 45.2."
hypothetical_envelope: entry 2.710-2.790, target 3.070, stop 2.510 -> R/R 1.0
```

BBRI **murah** tapi **sedang turun** (return 5d −1.8%). Sistem menolak mengejar pisau
jatuh. Bahkan andai masuk, R/R cuma **1.0** (di bawah floor 1.4). Jadi:

> **Di pasar turun: saham berkualitas sedang TURUN (gagal `no_momentum_confirmation`),
> saham yang NAIK overvalued (gagal `EXTREME_OVERVALUATION`). Irisan "naik ∩ wajar ∩
> R/R ≥ 1.4" = KOSONG → 0 BUY.**

Blue chip pembanding hari ini, semuanya HOLD, semua R/R null:
BBRI (0.64× FV) `no_momentum_confirmation`; BMRI (0.59× FV), ANTM (0.84×), TLKM (0.89×)
→ `rr_too_low` + `invalid_entry_range`.

**Filosofi sistem vs keinginan user.** Sistem ini **swing konservatif
fundamental+teknikal**. Secara desain ia **akan selalu menghindari roket spekulatif**
seperti BREN. Kalau user ingin menangkap BREN/BRPT, itu **sistem yang berbeda**
(momentum/gorengan murni) dengan profil risiko berbeda. Gap antara *yang user inginkan*
(nangkap big mover) dan *yang sistem lakukan* (swing aman) adalah **akar kebingungan** —
bukan bug.

**Trade-off jujur.** Sistem yang menangkap BREN/BRPT **juga** akan menangkap banyak yang
crash 50% secepat naiknya. Sistem yang aman dari crash **juga** melewatkan sebagian
roket. Tak bisa dua-duanya. Riset internal (`profit_over_quality_philosophy_2026-07-06.md`)
menemukan: literatur momentum untuk **emerging market condong NEGATIF** — momentum lemah
di Indonesia, value/fundamental lebih konsisten. Dan sinyal "harga+volume naik tajam"
secara teknikal **tak bisa dibedakan** dari pump-and-dump tanpa filter fundamental.

---

### Dimensi 4 — Apakah Sistem Benar-Benar Rusak? (Cerita Sebenarnya)

Fungsi inti **jalan**: kalkulasi teknikal benar, pipeline selesai end-to-end,
1.134 test lulus, sinyal terbentuk saat kondisi terpenuhi. Ini bukan "genuinely broken".

Tapi ledger realized (`backtest_memory.jsonl`) menceritakan yang sebenarnya:

**2 MENANG:**
- NZIA **+33%** (entry 4 Jun, target kena 1 hari) — small-cap momentum pop
- MBSS **+20%** (entry 4 Jun, target kena 6 hari) — small-cap momentum pop

**~21 KALAH (hampir semua `stop_hit`):**
- Terkonsentrasi **10–19 Juni** (jendela crash)
- Didominasi **BMRI & DMAS yang dibeli BERULANG saat turun** (10, 16, 18, 19 Jun) —
  averaging down ke pisau jatuh, kena stop tiap kali (−1.7% s/d −6.7%)

**Insight menohok:** Kegagalan nyata sistem **BUKAN kelewat konservatif** — sebaliknya,
di Juni ia **kelewat agresif**, menembak BUY beruntun ke pasar yang sedang runtuh.
Stop-loss-nya **bekerja** (kerugian terbatas ~−4% rata-rata), tapi **timing entry**-nya
buruk (beli saat downtrend). "0 BUY" sekarang = sistem **sudah belajar** (di-*harden*)
untuk tidak menangkap pisau jatuh. BMRI & DMAS — persis saham yang dulu bikin bleeding —
sekarang dengan benar dapat HOLD.

**Apakah ia pernah menangkap trade sehat?** Ya — 2 kemenangan itu. Ironisnya keduanya
justru momentum kecil cepat, bukan blue-chip. Tapi 2 dari 23 (win rate 8.7%) **bukan
sampel yang cukup untuk menyimpulkan apa pun** — apalagi mayoritas terjadi dalam satu
rezim (crash).

---

### Dimensi 5 — Jalan Menuju Berhasil

**🔴 P0 — Bangun kemampuan MENGUKUR edge (bukan memaksa output).** Godaan naif:
"longgarkan R/R floor / preflight biar ada BUY." **Jangan.** Itu persis kesalahan yang
menghasilkan 21 kekalahan. Setup R/R ~1.0 yang diblok sekarang **memang** reward-to-risk
jelek (stop lebar karena ATR tinggi, upside ke resistance pendek) — itu pasar
memberitahu bahwa ini waktu buruk untuk swing long. **P0 sebenarnya: harness backtest
historis as-of.** `diagnostic_gate_reopen` sendiri menyimpulkan kemampuan BUY end-to-end
"belum terbukti di tape live" karena tak ada tape sehat untuk diuji. Sampai bisa
me-replay 6–12 bulan lintas rezim (bull/sideways/bear), setiap keputusan tuning =
menembak dalam gelap. **Ini prasyarat untuk P1/P2 mana pun.**

**🟠 P1 — Putuskan intent, uji paralel (jangan flip filosofi buta).** Branch
`experiment/momentum-only` sudah ada sebagai wadah jujur. Jalankan varian
"momentum/profit-weighted" **berdampingan** dengan sistem hybrid, bandingkan **outcome
realized ke depan**, bukan asumsi. Dengan track record 2W/21L, mengganti filosofi tanpa
data pembanding **sama berisikonya** dengan mempertahankannya tanpa data pembanding.
- Ingin tetap **konservatif**: terima bahwa melewatkan BREN adalah **HARGA** dari
  menghindari crash. 0 BUY di tape post-crash adalah fitur, bukan bug.
- Ingin lebih **agresif**: perluas Momentum Play (kini 0× menyala) — tapi hanya setelah
  P0 bisa mengukur apakah itu menambah edge atau menambah kekalahan.

**🟡 P2 — Perbaikan spesifik (SETELAH P0 ada):**
1. **Data staleness**: XLSX 4 hari (DEGRADED, −10 ke semua skor). Refresh sebelum run.
   → `uv run python main.py` untuk data segar.
2. **Feed harga DSSA anomali** (px 815 vs harga riil) — verifikasi pipeline harga untuk
   saham split/high-price. Potensi salah-hitung valuasi.
3. **GARCH non-stationary** (log run hari ini: `persistence=1.0000`, ATR di-cap ke 3×
   klasik berulang). Cap-nya bekerja sebagai pengaman, tapi di rezim ekstrem model GARCH
   degenerate → ATR lebar → stop lebar → `rr_too_low`. Pertimbangkan fallback ke ATR
   klasik saat GARCH IGARCH-boundary terdeteksi.
4. **Double-gate preflight vs envelope** (sudah didokumentasikan sebagai intended di
   `gate_reopen`): buka kembali HANYA jika P0 menunjukkan setup mean-reversion pra-bounce
   punya edge yang terlewat.

---

### FINAL DIAGNOSTIC VERDICT

```
PRIMARY DIAGNOSIS: KOMBINASI
[x] Bekerja benar tapi konservatif  -> untuk BREN/DSSA/CUAN/BUMI (spekulatif overvalued)
[x] Mismatched ke intent user       -> sistem swing-aman != pemburu roket spekulatif
[~] Over-filtered (no output nyata)  -> nyata, TAPI tape-driven, mostly by-design
[ ] Genuinely broken                -> TIDAK (fungsi inti OK, 1134 test lulus)

SAHAM BREN/BRPT/PTRO/DSSA - ditolak dengan benar? MIXED, condong YA
  BREN/DSSA: benar (13.5x / 6%-float, EXTREME_OVERVALUATION)
  BRPT/PTRO: borderline (geometri/R-R, bukan spekulasi)

"NO OUTPUT": Root cause = irisan (naik ∩ wajar ∩ R/R>=1.4) kosong di tape
  pasca-crash + CIO HOLD tidak memancarkan envelope. Severity: High.
  Fix = P0 (ukur edge), BUKAN longgarkan threshold.

TEMUAN PALING MENGGANGGU:
  Track record 2W/21L, dan 21 kekalahan itu BUY berulang ke BMRI/DMAS saat
  crash Juni. Kekhawatiran user (kelewat konservatif) berlawanan 180 derajat dengan
  kelemahan nyata sistem (dulu kelewat agresif; kini belum terbukti punya edge).

PATH - PRIORITAS:
  P0: Harness backtest as-of (ukur edge lintas rezim) - prasyarat semua tuning
  P1: Uji momentum-only PARALEL, bandingkan realized (branch sudah ada)
  P2: Refresh XLSX, feed DSSA, GARCH fallback, review double-gate
```

### Jawaban jujur untuk "Apakah project saya gagal?"

**Tidak, project-mu tidak gagal — tapi ia juga belum terbukti berhasil, dan itu
pertanyaan yang lebih penting.** Menolak BREN (13.5× fair value, free float 5%) bukan
kegagalan; itu justru sistem melakukan tepat yang seharusnya — menolak mengejar move
spekulatif yang sudah lari dan detached dari fundamental. Yang kamu rasakan saat BREN
naik 50% adalah **FOMO / biaya peluang**, bukan kerugian nyata — entry aman sudah lewat,
dan mengejarnya adalah cara trader meledak. Masalah "no output" itu nyata, tapi
penyebabnya bukan filter yang rusak: di tape pasca-crash ini, saham bagus sedang turun
dan saham yang naik overvalued, sehingga tidak ada swing long dengan risk/reward layak —
dan itu, sebetulnya, pasar memberitahu bahwa ini waktu buruk untuk memaksa masuk. Bukti
paling penting bukan soal BREN sama sekali: track record realized-mu **2 menang / 21
kalah**, dan 21 kekalahan itu adalah sistem membeli BMRI & DMAS berulang saat crash Juni
— jadi kelemahan aslinya dulu **terlalu agresif**, dan "0 BUY" sekarang adalah koreksi
yang sehat, bukan kerusakan baru. Langkah konkret menuju berhasil bukan melonggarkan
threshold biar nangkap BREN (itu mengulang 21 kekalahan) — melainkan membangun **harness
backtest historis** supaya kamu akhirnya bisa mengukur apakah sistem ini punya edge sama
sekali, lalu menguji filosofi momentum-mu **berdampingan** dengan yang sekarang dan
membiarkan **outcome realized** yang memutuskan, bukan FOMO satu minggu.
