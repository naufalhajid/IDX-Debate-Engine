## Analisis Pergerakan Harga Lintas 20 Snapshot XLSX (Apr-Jul 2026)

- Tanggal analisis: 2026-07-07
- Data: 20 file `output/IDX Fundamental Analysis *.xlsx` (23 Apr -> 2 Jul 2026), sheet
  `stock-prices`, 957 ticker. Harga "hari ini" = live yfinance **per 3 Jul** (data IDX
  belum ter-update ke 6-7 Jul; gerakan 2 hari terakhir belum tercakup).
- Pelengkap `360_diagnostic_rejected_stocks_2026-07-06.md`. Menjawab pertanyaan user:
  "saham mana yang harganya bergerak bagus?" dan "berapa % kenaikan dari harga terbawah?"

### Caveat metode
- Close Price XLSX = harga scraped point-in-time per snapshot (tidak split-adjusted lintas
  snapshot -> lonjakan/tebing besar ditandai `split?`).
- `ADT proxy` = median(harga x volume) — untuk peringkat likuiditas **relatif**, bukan
  angka absolut (satuan volume XLSX tak dipastikan).
- Trough (dasar) = harga terendah yang tercatat di 20 snapshot; bukan tentu dasar
  intraday sebenarnya.

---

### 1) Saham yang BENAR-BENAR bergerak bagus (naik kuat + likuid + mulus)

Kriteria: return Apr->Jul > 10%, ADT proxy >= 5bn, tanpa anomali split. Urut kualitas
tren (return / kedalaman drawdown).

| Saham | Return Apr->Jul | Max DD | Up-ratio | ADT proxy | Indeks |
|---|---|---|---|---|---|
| **GULA** | +68.1% | -14.1% | 58% | ~15bn | — |
| **MARK** | +22.6% | -10.3% | 63% | ~15bn | — |
| **BSML** | +15.6% | -5.7% | 47% | ~7bn | — |
| **MAPI** | +16.5% | **-6.9%** | 47% | ~45bn | **LQ45** |
| **HATM** | +22.2% | -15.2% | 58% | ~7bn | — |
| **MBSS** | +17.7% | -18.2% | 32% | ~9bn | — |
| **GGRM** | +11.3% | -12.9% | 42% | ~19bn | **LQ45** |

Top kenaikan absolut (dikesampingkan karena mikro-cap ADT <2bn / anomali split, tak
tradeable di ukuran swing): FORU +144% (split), ADES +92%, BAPA +86%, RGAS +84%.

**Catatan:** MAPI adalah satu-satunya saham yang masih lolos filter sistem pada run
2026-07-06 (score 75.5) — dan ternyata pendaki paling bersih di antara blue chip
(drawdown hanya -7%). Sistem menunjuk ke jenis saham yang benar.

---

### 2) Saham yang ditolak sistem — bounce dari DASAR ke kini (per 3 Jul)

| Saham | Dasar (tgl) | Kini | **Dari dasar** | vs Puncak | vs April | Float |
|---|---|---|---|---|---|---|
| **DSSA** | 432 (28 Mei) | 820 | **+89.8%** | -63.6% | -63.6% | 6% HI |
| **BREN** | 2.450 (22 Mei) | 3.400 | **+38.8%** | -31.0% | -31.0% | 5% HI |
| **RAJA** | 3.050 | 3.940 | **+29.2%** | -15.5% | -15.5% | 36% |
| **AMMN** | 2.800 | 3.500 | **+25.0%** | -33.0% | -33.0% | 20% |
| **CUAN** | 505 | 610 | +20.8% | -56.4% | -56.4% | 15% |
| **BNBR** | 93 | 105 | +12.9% | -52.3% | -51.4% | — |
| **PTRO** | 3.490 | 3.860 | +10.6% | -37.5% | -37.5% | 28% |
| **TPIA** | 1.615 | 1.785 | +10.5% | -72.0% | -71.0% | 9% HI |
| **BUMI** | 135 | 139 | +3.0% | -42.1% | -39.6% | 40% |
| **CDIA** | 595 | 610 | +2.5% | -48.7% | -45.0% | — |
| **BRPT** | 1.485 | 1.483 | -0.1% | -35.5% | -32.3% | 29% |

Di window penuh (Apr->Jul) saham-saham ini adalah **pecundang terburuk**: BREN -34%,
BRPT -32%, PTRO -38%, DSSA -64%, CUAN -56%, TPIA -71%.

---

### 3) Temuan menentukan — bounce spekulatif itu MENIPU

**Premis "naik 30-50%" itu BENAR** untuk sebagian (DSSA +90%, BREN +39%, RAJA +29% dari
dasar). Tapi:

1. **Setiap bounce adalah pantulan dari jurang -60 s/d -80%.** DSSA naik 90% dari dasar
   tapi **masih -64% vs April**. Untuk menangkapnya kamu harus beli **persis di dasar**
   (gorengan float 6% yang saat itu sudah anjlok 60%+) — menangkap pisau jatuh.
2. **Bandingkan dengan good movers** — mereka bounce dari dasar **sebesar** spekulan
   TAPI net untung dan di dekat/atas puncak:

| | Dari dasar | vs Puncak | **vs April** |
|---|---|---|---|
| GULA | +76.7% | -8.9% | **+73.7%** (untung) |
| HATM | +50.7% | +4.7% | **+27.8%** (untung) |
| MBSS | +33.3% | +0.0% | **+17.7%** (untung) |
| MARK | +26.9% | +1.5% | **+24.5%** (untung) |
| MAPI | +24.7% | -0.3% | **+16.1%** (untung) |
| — vs — | | | |
| DSSA | +89.8% | -63.6% | **-63.6%** (rugi) |
| BREN | +38.8% | -31.0% | **-31.0%** (rugi) |

**Perbedaan menentukan:** good movers (GULA/MAPI/MARK/HATM) bounce **sebesar**
DSSA/BREN — tapi **NET UNTUNG vs April**, tren mulus. Spekulan bounce tapi **masih rugi
30-64%** (crash-lalu-mantul).

### Kesimpulan
- "Naik bagus" sesungguhnya = pendaki mulus net-untung (GULA +74%, MAPI +16% vs April),
  bukan crash-lalu-mantul.
- Memburu bounce dasar (DSSA +90%) = **strategi berbeda** (bottom-fishing gorengan),
  risiko berbeda — uji paralel (lihat `360_diagnostic_action_checklist_2026-07-06.md` P1),
  jangan diasumsikan.
- Sistem menghindari -64% DSSA; pick-nya yang bertahan (MAPI) justru jenis yang benar.
