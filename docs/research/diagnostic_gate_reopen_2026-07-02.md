# Diagnostic Gate Re-Open (V0.3) — 2026-07-02

## Pertanyaan diagnostik

Sejak hardening akhir Juni 2026 pipeline menghasilkan 0 BUY (semua HOLD 0.40).
Apakah konfigurasi yang sama masih **bisa** menghasilkan BUY end-to-end bila
regime tidak memblokir (bukan DEFENSIVE / BEAR_STRESS)?

## Metode

Tool baru: `scripts/diagnostic_gate_reopen.py` (tidak mengubah kode produksi —
override murni monkeypatch runtime di dalam proses script):

- Rule-based `detect_market_regime()` → snapshot sintetis **NORMAL**
  (vol 1.2%/hari, close di atas seluruh MA).
- HMM `regime_gate_node` → **SIDEWAYS**, `should_trade=True`, `msci_override=False`.
- Seluruh tulisan pipeline dialihkan ke `output/diagnostics/gate_reopen_<ts>/`
  (`configure_output_dir`, `_WATCHLIST_LOG_PATH`, `DEFAULT_MEMORY.path`,
  `evaluate_memory` di-no-op) — ledger produksi tidak tersentuh.
- Data live yfinance per 2026-07-02; pool 34 ticker likuid lintas sektor.

Dua fase: **Phase A** sweep `_compute_trade_envelope` deterministik
(DEFENSIVE vs NORMAL, tanpa LLM; R/R hipotetis dari counterfactual envelope
V0.2 bila ditolak) → **Phase B** pipeline penuh (debate + governor) pada
kandidat terbaik.

## Hasil

### Phase A — envelope sweep (34 ticker)

Lolos envelope: **4/34** — CPIN (R/R 1.72), SIDO (1.60), EXCL (1.50),
PTBA (1.44). Keempatnya setup mean-reversion (RSI < 40 → gate momentum
di-skip sesuai desain F12). Sisanya ditolak: `rr_too_low` mayoritas
(resistance pasca-crash terlalu dekat + ATR tinggi → target pendek, stop
lebar), `no_momentum_confirmation` (return 5d negatif merata), sebagian
`stop_inside_noise`.

Temuan penting: shift regime DEFENSIVE→NORMAL **nyaris tidak mengubah hasil
envelope** (hanya multiplier ATR stop; contoh ICBP R/R 0.26→0.30). Blokir 0
BUY bukan di geometri envelope per-regime.

### Cross-check preflight (`_run_tradeability_preflight`)

Preflight memakai surrogate berbeda dari envelope: `gap = price − low_20d`
vs floor `1.0×ATR` (envelope: `entry_high − stop`, stop di bawah swing low −
0.5·ATR). Hasil pada 4 ticker yang lolos envelope:

| Ticker | gap (price−low20) | 1.0×ATR | Status preflight |
|---|---|---|---|
| PTBA | 20 | 88 | **reject** |
| CPIN | 100 | 166 | **reject** |
| EXCL | 100 | 117 | **reject** |
| SIDO | 14 | 12 | conditional (borderline) |

**Tension desain**: envelope sengaja meloloskan mean-reversion di dekat low
(RSI < 40), tetapi saham yang sedang berada di dekat low 20d-nya hampir pasti
punya `price − low_20d < 1.0×ATR` → preflight menolaknya secara struktural.
Profil satu-satunya yang bisa menembus keduanya: **confirmed bounce** — sudah
memantul ≥ 1×ATR dari low 20d, namun R/R ke resistance masih ≥ 1.4 (dan bila
RSI > 40, return 5d harus positif). Sempit tapi koheren.

### Phase B — pipeline penuh (CPIN + SIDO; rule=NORMAL, HMM=SIDEWAYS)

Run: `gate_reopen_20260702_162056`, 551 detik, LLM Pro 1/200 + Flash 8/2000.

| Ticker | Verdict | Governor | Titik mati |
|---|---|---|---|
| CPIN | HOLD 0.40 | reject: `preflight_noise_reject` | Preflight (0 LLM call) |
| SIDO | INSUFFICIENT_DATA 0.16 | reject: `confidence_16pct_below_minimum` | Confidence floor 25% — scout membaca breaking news bearish, evidence collapse |

- **Override regime terverifikasi bekerja**: state SIDO membawa HMM=SIDEWAYS,
  tidak ada defensive clamp, tidak ada `trading_halted`.
- **0 BUY** — tetapi bukan karena lapisan regime.

## Kesimpulan

1. **Lapisan regime bukan satu-satunya penyebab 0 BUY**, dan mekanisme
   override-nya bekerja benar (clamp + HMM halt terbuka sesuai patch).
2. Blocker aktif di tape 2026-07-02: (a) geometri pasca-crash → `rr_too_low`
   massal; (b) preflight noise surrogate menolak mean-reversion yang justru
   diloloskan envelope; (c) confidence floor 25% pada evidence yang memang
   bearish. Ketiganya pre-LLM/evidence-level — perilaku konservatif yang
   defensible, bukan bug.
3. **Kemampuan BUY end-to-end belum terbukti di tape live** — karena tidak ada
   kandidat valid hari ini, bukan karena gate macet. Eskalasi HMM=BULL tidak
   informatif (blocker berada sebelum threshold konsensus/CIO).

## Tindak lanjut

- Re-run diagnostik saat tape menghasilkan kandidat *confirmed bounce* —
  pantau via counterfactual ledger V0.2 (`watchlist_log.jsonl`): baris dengan
  `hypothetical_envelope.risk_reward_ratio ≥ 1.4` + preflight clean adalah
  sinyal re-test.
- V1.x: tinjau surrogate preflight (opsi: pakai stop struktural envelope,
  bukan `low_20d` mentah) ATAU dokumentasikan sebagai intended double-gate;
  keputusan mempengaruhi berapa banyak setup mean-reversion yang pernah bisa
  sampai ke debate.
- Alternatif pembuktian penuh: replay historis pra-crash (butuh harness
  as-of untuk scout non-teknis — di luar scope V0.3).

## Artefak

- `output/diagnostics/gate_reopen_20260702_161907/` — Phase A pool default 10 ticker.
- `output/diagnostics/gate_reopen_20260702_162015/` — Phase A sweep lebar 24 ticker.
- `output/diagnostics/gate_reopen_20260702_162056/` — Phase B penuh (CPIN, SIDO) + `DIAGNOSTIC_REPORT.md`.
