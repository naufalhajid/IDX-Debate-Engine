# Riset: "Tidak Peduli Kualitas Saham, Yang Penting Untung" (V0.1) — 2026-07-06

## Pertanyaan

User mengusulkan: ubah filosofi sistem supaya tidak peduli apakah saham itu
"bagus" secara fundamental — yang penting bisa menghasilkan profit. Diminta
riset dulu sebelum implementasi apa pun.

## Temuan 1 (internal): sistem SUDAH punya jalur ini — "Momentum Play"

`services/debate_prompts/cio_judge.txt` STEP 3 (CONFLICT RESOLUTION) sudah
mendefinisikan persis skenario ini:

```
Fundamental + Technical == FAIL/PASS ->
  IF Volume breakout confirmed (volume_surge_ratio >= 1.5 AND return_5d_pct > 0):
    -> Lean BUY (Momentum Play, size 50%)
    -> "Momentum trade — entry is purely technical, no fundamental support."
```

Artinya: tim sebelumnya sudah mengantisipasi ide ini, dan sudah membangun
jalur eksplisit untuk "beli walau fundamental gagal, asal teknikal +
volume + R/R meyakinkan" — dengan ukuran posisi diperkecil 50% sebagai
kontrol risiko bawaan.

**Konflik laten (bukan bug pasti, tapi tension nyata)**: `risk_governor.py`
punya hard-reject code `"overvalued"` yang dipicu independen dari
`momentum_play`. Dicek di `debate_chamber.py:4377-4382`:

```python
risk_overvalued = False
if fair_value_high:
    risk_overvalued = current_price > fair_value_high   # dipakai kalau tersedia
elif fair_value:
    risk_overvalued = current_price > fair_value          # fallback titik-estimasi
```

`risk_governor.py` tidak pernah membaca field `momentum_play` (dikonfirmasi
grep, nol match) — jadi kalau harga di atas `fair_value_high` (ujung atas
rentang fair value), trade DITOLAK KERAS oleh risk_governor terlepas dari
alasan Momentum Play CIO, terlepas juga dari ukuran posisi 50% yang sudah
dirancang sebagai mitigasi.

**Tapi — dicek empiris dulu sebelum diklaim sebagai bug nyata**: digrep
seluruh `output/debates/*.json` dan ledger watchlist untuk
`"momentum_play": true` — **nol kemunculan**. Jalur ini tampaknya belum
pernah benar-benar terpicu di histori sistem. Jadi konflik di atas sifatnya
laten/teoretis, bukan "diam-diam membunuh trade nyata" — belum ada bukti ia
pernah dieksekusi sama sekali, entah karena kombinasi kondisinya jarang
terjadi di data IDX, atau kandidatnya sudah tersaring gate lain (preflight,
liquidity, ARA/ARB) sebelum sempat sampai ke CIO.

## Temuan 2 (eksternal): risiko spesifik IDX — pola gorengan = pola momentum murni

Riset pump-and-dump/"saham gorengan" di BEI menunjukkan tanda teknikal
persis:
- Lonjakan harga tajam + lonjakan volume TANPA katalis fundamental/berita
  yang jelas ("fake liquidity")
- Pola "climax top": kenaikan harga+volume ekstrem di puncak, lalu ambruk
  cepat — siklus berulang
- Target favorit bandar: market cap kecil (< ~Rp500 miliar), laporan
  keuangan lemah/rugi, tanpa prospek bisnis jelas
- Rekomendasi baku industri untuk menghindarinya: berpegang pada indeks
  likuid resmi (IDX30/LQ45), verifikasi via keterbukaan informasi resmi

**Implikasi langsung**: sinyal "harga naik tajam + volume naik tajam" —
yaitu PERSIS sinyal yang dicari strategi momentum murni — secara teknikal
**tidak bisa dibedakan** dari pola pump-and-dump tanpa filter fundamental/
kualitas. Kalau sistem berhenti peduli fundamental sepenuhnya, sistem
kehilangan satu-satunya pembeda antara "breakout asli" dan "digoreng bandar".

Ini juga menjelaskan kenapa dua mekanisme yang sudah ada di codebase BUKAN
sekadar penilaian "kualitas" yang bisa dibuang begitu saja, tapi justru
pertahanan anti-manipulasi yang sudah selaras dengan rekomendasi industri:
- `check_free_float()` di `quant_filter/pipeline.py` (Free Float
  Manipulation Penalty, -20 skor kalau risk HIGH)
- `is_lq45_ticker()` — persis rekomendasi "berpegang pada IDX30/LQ45"

## Temuan 3 (eksternal): literatur faktor momentum vs kualitas/value

- Momentum historis unggul di rolling 1-3-5 tahun DEVELOPED market — tapi
  horizon itu jauh lebih panjang dari swing 5-20 hari sistem ini.
- **Temuan paling relevan**: "Size and momentum strategies generally fail
  to generate superior returns in **emerging markets**, while the value
  effect exists in all markets except Brazil." — momentum/size justru
  cenderung TIDAK bekerja di emerging market (termasuk Indonesia), sementara
  efek value/fundamental lebih konsisten bertahan.
- Konsensus akademis: momentum memberi nilai tambah kalau **dipadukan**
  dengan value/quality, bukan menggantikannya sepenuhnya.

## Sintesis

Ide "profit tanpa peduli kualitas" bukan ide aneh — itu keluarga strategi
sah (pure quant/momentum). Tapi tiga temuan di atas menunjuk arah yang sama:

1. Sistem ini sudah mencoba versi terbatasnya (Momentum Play) dan itu nyaris
   tidak pernah terpakai dalam praktik — pertanyaan "kenapa" itu sendiri
   layak dijawab sebelum memperluas cakupannya.
2. Untuk pasar IDX spesifik, "abaikan fundamental" berarti melepas
   pertahanan utama terhadap pola manipulasi yang justru meniru sinyal
   momentum murni.
3. Bukti akademis untuk emerging market condong ke ARAH SEBALIKNYA dari
   yang diusulkan — momentum lemah, value/fundamental tetap relevan.

## Rekomendasi (belum dieksekusi — untuk didiskusikan)

**Tidak disarankan**: menghapus/menonaktifkan `fundamental_scout` atau
melucuti gate anti-manipulasi (`check_free_float`, `is_lq45_ticker`,
ARA/ARB check) — ketiganya beda tujuan dari "menilai saham bagus/jelek",
dan literatur+risiko IDX-spesifik sama-sama menentang pelucutan total.

**Disarankan, dua langkah terpisah**:
1. **Langkah kecil, cepat, low-risk**: putuskan nasib konflik laten
   Momentum Play vs `risk_overvalued` — apakah `risk_governor` seharusnya
   mengecualikan trade yang ditandai `momentum_play=true` dari hard-reject
   `overvalued` (mempercayakan kontrolnya ke ukuran posisi 50% + R/R
   threshold CIO), atau tetap seperti sekarang. Ini murni memperbaiki
   konsistensi arsitektur, terlepas dari keputusan filosofi besar.
2. **Langkah besar, uji empiris, bukan ganti langsung**: kalau user tetap
   ingin menguji "profit-first, abaikan fundamental" secara serius, cara
   yang jujur adalah lewat infrastruktur eksperimen paralel yang sudah ada
   di roadmap (Fase 2, `over_engineering_remediation_checklist.md`) — jalankan
   varian "momentum/profit-weighted" berdampingan dengan sistem hybrid
   sekarang, bandingkan **outcome realized ke depan**, bukan asumsi. Sistem
   ini sendiri baru punya track record 2W/21L — mengganti filosofi tanpa
   data pembanding sama berisikonya dengan mempertahankan filosofi lama
   tanpa data pembanding.

## Sumber

- [Saham Gorengan: Ciri-Ciri dan Cara Menghindarinya](https://www.maybanktrade.co.id/berita/saham-gorengan-ciri-ciri-dan-cara-menghindarinya/)
- [Mengenal Konsep Pump and Dump dalam Saham](https://pina.id/artikel/detail/mengenal-konsep-pump-and-dump-dalam-saham-1ghgd5afz6c)
- [Saham Gorengan Adalah: Ciri-Ciri dan Kenapa Bahaya bagi Portofolio Anda 2026](https://pluang.com/akademi/berita-analisis/apa-itu-saham-gorengan)
- [Waspadai 7 Ciri Saham Gorengan Berikut Ini](https://mstock.miraeasset.co.id/blog/ciri-saham-gorengan/)
- [Momentum Factor Effect in Stocks - Quantpedia](https://quantpedia.com/strategies/momentum-factor-effect-in-stocks)
- [Quality, Factor Momentum, and the Cross-Section of Returns](https://alphaarchitect.com/cross-section-of-returns/)
- [Momentum factor investing: Evidence and evolution](https://alphaarchitect.com/momentum-factor-investing/)
- [Value, Quality, Momentum, And Lower Volatility In One Emerging Markets Fund](https://247wallst.com/investing/2026/05/16/value-quality-momentum-and-lower-volatility-in-one-emerging-markets-fund/)
