## P0 — Pengukuran Edge via Backtest (2026-07-07)

Tindak lanjut `360_diagnostic_action_checklist_2026-07-06.md` P0. Tujuan: menjawab
"sistem ini punya edge trading atau tidak?" — karena track record live 2W/21L (1 crash)
bukan sampel. Semua deterministik (OHLCV yfinance + pandas, **tanpa LLM**).

### Metode
- `scripts/historical_backtest.py` — replay gate entry produksi (RSI<=70, ATR%<=4%,
  dekat MA50, konfirmasi momentum return_5d) + geometri envelope (R/R 2:1, resistance
  tiers, cap +10%), evaluasi 20 hari (win=target dulu, loss=stop dulu). Non-overlapping.
- Caveat drift (item 0.3): ATR%_max 0.04 (produksi 0.05), REGIME_ATR_MULT fix 2.5 (tanpa
  regime scaling). Sinkronkan sebelum dipakai untuk keputusan final.

---

### 0.2 — Baseline: envelope teknikal SENDIRIAN (30 saham likuid, 2 thn)

| Metrik | Nilai |
|---|---|
| Trade closed | 539 |
| **Win rate** | **34.1%** (184W / 274L / 81 flat) |
| **Avg PnL/trade** | **-0.25%** |
| Avg holding | 12.9 hari |

Dengan target R/R 2:1 tapi win 34% -> expectancy **~breakeven-ke-negatif**, dan
**negatif setelah biaya transaksi** (~0.2-0.3% bolak-balik IDX). Gagal ambang "berhasil"
(win >= 40% & avg R >= 1.5).

Dispersi per-saham besar: menang jelas di MAPI (55%, +3.5%), PGAS (58%, +2.4%), MDKA,
UNTR, INCO, ITMG; hancur di GGRM (6%, -3.7%), SIDO (13%), BRIS (21%), UNVR (31%).

### 0.4 — Apakah lapis FUNDAMENTAL menambah edge? (IDX80 by Piotroski, 2 thn)

| Grup | Trade | Win rate | Avg PnL/trade |
|---|---|---|---|
| **KUAT** (Piotroski avg 8.3) | 233 | **39.1%** | **+0.54%** |
| **LEMAH** (Piotroski avg 4.3) | 239 | **38.5%** | **+0.53%** |

**Fundamental kuat vs lemah = outcome identik (selisih = noise).** Kualitas fundamental
(Piotroski 8-9 vs 3-4) **tidak memisahkan** hasil swing sama sekali saat entry pada
setup teknikal yang sama.

---

### VONIS P0

1. **Envelope teknikal sendirian: ~breakeven, tak ada edge terbukti** (win 34-39%,
   avg -0.25% s/d +0.54% tergantung universe; net ~nol setelah biaya).
2. **Lapis fundamental (Piotroski) TIDAK menambah return-edge** — basket kuat & lemah
   identik. Selaras dengan validasi IC internal (`config.py`: "Harvey/Liu/Zhu bar: NO
   signal passed").
3. **Implikasi diagnosis:** nilai lapis fundamental itu **DEFENSIF (veto), bukan OFENSIF
   (pemilih pemenang)** — ia menyelamatkanmu dari BREN/DSSA (-64%), tapi tidak membuat
   saham "bagus" lebih untung untuk swing. Bobot fundamental 85% di composite score
   mungkin over-weighted untuk kontribusi return-nya; pertimbangkan reframe jadi
   risk-filter, bukan return-ranker.
4. **2W/21L bukan sekadar sial crash** — konsisten dengan fondasi ~breakeven yang
   kebetulan tertangkap drawdown.

### Yang BELUM teruji (jujur — jangan over-claim)
- **Lapis debate/CIO/sentiment penuh** (bagian kualitatif, sadar-berita) belum diuji —
  butuh LLM backtest terbatas (mahal, ditunda). Ini satu-satunya tempat edge ofensif
  masih *mungkin* ada.
- **Seleksi berbasis VALUASI** (bukan Piotroski) belum diisolasi — composite 48% bobotnya
  valuation. Uji stratifikasi by MoS/valuation gap berikutnya.
- Edge mungkin ada di **subset sempit** (MAPI/PGAS-type large-cap momentum), bukan lintas
  universe — layak diselidiki (P1: fokus universe).

### Guardrail (dipertegas oleh data ini)
JANGAN longgarkan gate/floor. Fondasinya ~breakeven — melonggarkan = memperbanyak trade
nol-edge. Naikkan selektivitas (atau persempit universe ke subset ber-edge), bukan
turunkan.

Artefak: `output/historical_backtest/summary.json`.
