# V2.1 — Ablation Struktural: Debate vs Single-Agent vs Quant-Only

> Dihasilkan oleh `uv run python -m scripts.ablation_v2_1_report` terhadap data
> di `output/ablation_v2_1/` (gitignored, regenerable via
> `uv run python scripts/ablation_v2_1_run.py`). Salinan ini disimpan di
> `docs/research/` karena `output/` sepenuhnya di-gitignore (`/output/**` di
> `.gitignore:76`) — konvensi yang sama dengan laporan diagnostik lain di
> direktori ini (mis. `gap_analysis_report.md`).

**Cakupan**: perbandingan STRUKTURAL + BIAYA pada 25 ticker yang sama, hari yang sama (2026-07-04, regime DEFENSIVE), kode saat ini. BUKAN verdict performa — belum ada forward outcome untuk trade yang baru didebat hari ini (lihat scripts/ablation_study.py untuk perbandingan retrospektif berbasis outcome real: data-starved, n<30, lihat output/ablation/ablation_report.json).

## Ringkasan
- Total ticker: 25
- Agreement rate MENTAH (single vs multi): 9/25 (36%)
- Agreement rate KONDISIONAL (subset di mana quant-only/single/multi menyebut sesuatu actionable): 1/10
- Quant-only BUY: 1/25 (ERAA — satu-satunya lolos gate produksi penuh hari ini)
- Total waktu single-agent: 499s (20s/ticker rata-rata)
- Total panggilan LLM leg debat: 178 flash + 1 pro
- Divergensi single-vs-multi yang gate-driven: 12/12 (lihat ## Temuan Kunci)

## Tabel Perbandingan

| Ticker | Quant-Only | Single | Multi | Dissent | Rounds | Notes |
|--------|-----------|--------|-------|---------|--------|-------|
| BBCA | AVOID | HOLD (34%) | HOLD (40%) | 1 | 3 | no_momentum_confirmation |
| BBRI | AVOID | BUY (47%) | HOLD (40%) | 3 | 3 | rr_too_low |
| BBNI | AVOID | BUY (46%) | HOLD (40%) | 3 | 3 | no_momentum_confirmation |
| BMRI | AVOID | BUY (42%) | HOLD (40%) | 2 | 3 | rr_too_low |
| ICBP | AVOID | BUY (47%) | HOLD (40%) | 1 | 3 | rr_too_low |
| UNVR | AVOID | HOLD (37%) | HOLD (35%) | 1 | 1 | - |
| MYOR | AVOID | HOLD (58%) | HOLD (40%) | 2 | 3 | no_momentum_confirmation |
| GGRM | AVOID | BUY (52%) | HOLD (40%) | 2 | 3 | rr_too_low |
| ULTJ | AVOID | BUY (42%) | HOLD (40%) | 0 | 1 | rr_too_low |
| ERAA | BUY | HOLD (34%) | HOLD (40%) | 1 | 1 | rr_too_low |
| MAPI | AVOID | BUY (52%) | HOLD (40%) | 2 | 3 | rr_too_low |
| ACES | AVOID | HOLD (55%) | HOLD (40%) | 1 | 3 | rr_too_low |
| AMRT | AVOID | AVOID (81%) | HOLD (40%) | 2 | 3 | no_momentum_confirmation |
| MIDI | AVOID | HOLD (41%) | HOLD (40%) | 1 | 3 | no_momentum_confirmation |
| ADRO | AVOID | HOLD (37%) | HOLD (40%) | 0 | 1 | rr_too_low |
| PTBA | AVOID | HOLD (42%) | HOLD (40%) | 0 | - | - |
| ITMG | AVOID | HOLD (34%) | ERROR (-) | 0 | - | - |
| ANTM | AVOID | HOLD (36%) | HOLD (40%) | 2 | 3 | rr_too_low |
| TINS | AVOID | AVOID (42%) | HOLD (40%) | 0 | 1 | no_momentum_confirmation |
| BRPT | AVOID | AVOID (78%) | HOLD (40%) | 2 | 3 | stop_inside_noise |
| TPIA | AVOID | BUY (34%) | HOLD (40%) | 3 | 2 | stop_inside_noise |
| CUAN | AVOID | AVOID (91%) | ERROR (-) | 0 | - | - |
| TLKM | AVOID | HOLD (34%) | ERROR (-) | 0 | - | - |
| CPIN | AVOID | BUY (42%) | ERROR (-) | 0 | - | - |
| BREN | AVOID | AVOID (88%) | HOLD (40%) | 1 | 3 | rr_too_low |

## Kasus Berbeda (single vs multi)
- BBRI: single=BUY (47%) vs multi=HOLD (40%), dissent=3, rounds=3 [GATE-DRIVEN]
- BBNI: single=BUY (46%) vs multi=HOLD (40%), dissent=3, rounds=3 [GATE-DRIVEN]
- BMRI: single=BUY (42%) vs multi=HOLD (40%), dissent=2, rounds=3 [GATE-DRIVEN]
- ICBP: single=BUY (47%) vs multi=HOLD (40%), dissent=1, rounds=3 [GATE-DRIVEN]
- GGRM: single=BUY (52%) vs multi=HOLD (40%), dissent=2, rounds=3 [GATE-DRIVEN]
- ULTJ: single=BUY (42%) vs multi=HOLD (40%), dissent=0, rounds=1 [GATE-DRIVEN]
- MAPI: single=BUY (52%) vs multi=HOLD (40%), dissent=2, rounds=3 [GATE-DRIVEN]
- AMRT: single=AVOID (81%) vs multi=HOLD (40%), dissent=2, rounds=3 [GATE-DRIVEN]
- ITMG: single=HOLD (34%) vs multi=ERROR (-), dissent=0, rounds=- [ERROR]
- TINS: single=AVOID (42%) vs multi=HOLD (40%), dissent=0, rounds=1 [GATE-DRIVEN]
- BRPT: single=AVOID (78%) vs multi=HOLD (40%), dissent=2, rounds=3 [GATE-DRIVEN]
- TPIA: single=BUY (34%) vs multi=HOLD (40%), dissent=3, rounds=2 [GATE-DRIVEN]
- CUAN: single=AVOID (91%) vs multi=ERROR (-), dissent=0, rounds=- [ERROR]
- TLKM: single=HOLD (34%) vs multi=ERROR (-), dissent=0, rounds=- [ERROR]
- CPIN: single=BUY (42%) vs multi=ERROR (-), dissent=0, rounds=- [ERROR]
- BREN: single=AVOID (88%) vs multi=HOLD (40%), dissent=1, rounds=3 [GATE-DRIVEN]

## Temuan Kunci: Sumber Divergensi
Dari 12 ticker dengan verdict single vs multi yang berbeda (di luar 4 kasus ERROR koneksi), **12/12 (100%) ditandai kode gate deterministik** (no_momentum_confirmation, rr_implausible, rr_too_low, stop_inside_noise) — bukan hasil penalaran LLM yang lebih kaya di sisi multi-agent.
`rr_too_low`/`rr_implausible` berasal dari core/risk_governor.py (floor R/R tier-aware); `no_momentum_confirmation`/`stop_inside_noise` berasal dari validasi trade-envelope di services/debate_chamber.py (cek RSI/return_5d dan jarak stop vs ATR) — keduanya murni numerik, nol input LLM.
services/single_agent_analyzer.py tidak pernah memanggil risk_governor (dikonfirmasi via grep, nihil hasil) dan tidak punya validasi envelope sendiri, sehingga verdict BUY-nya tidak pernah punya kesempatan ditolak oleh gate yang sama. Artinya: pada sampel ini, nilai tambah yang teramati dari multi-agent bukan berasal dari "debat menemukan risiko yang lebih dalam", melainkan dari gate deterministik yang secara struktural hanya terpasang di jalur multi-agent — efek yang sama, dalam prinsip, bisa didapat lebih murah dengan menjalankan risk_governor + validasi envelope langsung di atas output single-agent, tanpa debat berputar-putar.

## Interpretasi
Agreement mentah tinggi kemungkinan besar didominasi kecocokan trivial (HOLD/HOLD, AVOID/AVOID) di bawah regime DEFENSIVE — lihat agreement KONDISIONAL di atas untuk sinyal yang lebih informatif. Pertanyaan yang benar-benar dijawab sesi ini bukan "mana rating yang benar" (itu tugas V3.1/V5 dengan forward evidence), melainkan apakah biaya debat penuh (rounds, dissent, flash+pro calls) terbukti sepadan — dan temuan kunci di atas menunjukkan bahwa pada sampel ini, gate deterministik yang jauh lebih murah sudah menjelaskan seluruh divergensi yang resolved. Verdict PERFORMA (rating mana yang benar) masih menunggu forward evidence — bukan pertanyaan yang bisa dijawab hari ini berapa pun jumlah debat yang dijalankan.

## Reliabilitas Leg Debat
4/25 ticker (ITMG, CUAN, TLKM, CPIN) gagal dengan "Connection error" di leg multi-agent selama run 25-ticker (~60-90 menit, banyak panggilan LLM berurutan) — tidak direplay ulang untuk menghindari biaya panggilan LLM tambahan (sesi ini sudah melewati ambang biaya kritis). Tingkat kegagalan transient 16% ini sendiri adalah bagian dari gambaran biaya/keandalan multi-agent yang dibandingkan terhadap single-agent (0 kegagalan dari 25 ticker, 499 detik total).
