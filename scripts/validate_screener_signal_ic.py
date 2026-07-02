"""Run screener signal IC tests with multiple-testing correction on local snapshots.

Two signal families share one Benjamini-Hochberg family:
  - fundamental: columns available in the weekly sweep XLSX archive
    (forward horizon = next available snapshot's Close Price)
  - technical: RSI(14) and volume-surge (raw + config-tiered scores) plus
    22d price momentum, computed from a yfinance daily panel at each
    snapshot date (forward horizon = 5 trading days, per the config TODO)

Decision rule (Harvey/Liu/Zhu): retain a signal only when mean IC > 0.05
and abs(t-stat) >= 2.57 (and it survives BH FDR 5%).
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quant_filter.config import CONFIG  # noqa: E402
from utils.technicals import compute_rsi  # noqa: E402

_HLZ_TSTAT_THRESHOLD = 2.57
_MIN_CROSS_SECTION = 8
_TECH_FORWARD_DAYS = 5
_TECH_PANEL_START = "2026-01-15"  # warmup for RSI14 / avg-volume-20d before first snapshot

FUNDAMENTAL_SIGNALS = [
    "price_to_equity_discount",
    "pbv_x_roe",
    "relative_pe_inverse",
    "eps_growth",
    "yearly_price_change",
    "composite_rank_inverse",
]
TECHNICAL_SIGNALS = ["rsi14", "rsi_score", "vol_surge", "vol_score", "price_mom_22d"]


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _xlsx_files(output_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("IDX Fundamental Analysis *.xlsx", "IDX_Fundamental_Analysis_*.xlsx"):
        files.extend(output_dir.glob(pattern))
    return sorted(set(files), key=lambda path: path.name)


def _snapshot_date(path: Path) -> pd.Timestamp | None:
    match = re.search(r"(\d{4}-\d{2}-\d{2})", path.name)
    return pd.Timestamp(match.group(1)) if match else None


def _numeric_col(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _prepare_frame(path: Path) -> pd.DataFrame:
    """Map the weekly-sweep XLSX schema to signal columns.

    The archive stores derived sweep columns (PBV x ROE, Relative PE, EPS
    Growth, Composite Rank, ...) — raw ROE/PBV/EPS/OCF columns do not exist,
    so signals are defined on what the sweep actually persists.
    """
    frame = pd.read_excel(path)
    frame["Ticker"] = frame["Ticker"].astype(str).str.replace(".JK", "", regex=False).str.upper()
    frame["Close Price"] = _numeric_col(frame, "Close Price")
    frame["price_to_equity_discount"] = _numeric_col(frame, "Price to Equity Discount (%)")
    frame["pbv_x_roe"] = _numeric_col(frame, "PBV x ROE")
    frame["relative_pe_inverse"] = -_numeric_col(frame, "Relative PE ratio (TTM)")
    frame["eps_growth"] = _numeric_col(frame, "EPS Growth")
    frame["yearly_price_change"] = _numeric_col(frame, "Yearly Price Change")
    # Rank 1 = best in the sweep; invert so "higher = better" like the others.
    frame["composite_rank_inverse"] = -_numeric_col(frame, "Composite Rank")
    return frame[["Ticker", "Close Price", *FUNDAMENTAL_SIGNALS]].copy()


def _spearman_ic(cross: pd.DataFrame, signal: str) -> float | None:
    subset = (
        cross[[signal, "forward_return"]]
        .replace([math.inf, -math.inf], pd.NA)
        .dropna()
    )
    if len(subset) < _MIN_CROSS_SECTION:
        return None
    if subset[signal].nunique() < 2 or subset["forward_return"].nunique() < 2:
        return None
    ic = subset[signal].corr(subset["forward_return"], method="spearman")
    if ic is None or not math.isfinite(float(ic)):
        return None
    return float(ic)


def _fundamental_ic(frames: list[pd.DataFrame]) -> dict[str, list[float]]:
    values: dict[str, list[float]] = {name: [] for name in FUNDAMENTAL_SIGNALS}
    for current, nxt in zip(frames, frames[1:]):
        forward = nxt[["Ticker", "Close Price"]].rename(columns={"Close Price": "next_close"})
        merged = current.merge(forward, on="Ticker", how="inner")
        merged["forward_return"] = (merged["next_close"] / merged["Close Price"]) - 1.0
        for signal in FUNDAMENTAL_SIGNALS:
            ic = _spearman_ic(merged, signal)
            if ic is not None:
                values[signal].append(ic)
    return values


# ── Technical signals (RSI / volume) from a yfinance daily panel ──────────────


def _rsi_tier_score(rsi: float) -> float:
    """Replicate the v3.3 asymmetric RSI scoring tiers (config rsi_weight_*)."""
    if rsi < CONFIG["rsi_accum_lo"]:
        return CONFIG["rsi_weight_oversold"]
    if rsi <= CONFIG["rsi_accum_hi"]:
        return CONFIG["rsi_weight_accum"]
    if rsi <= CONFIG["rsi_strong_hi"]:
        return CONFIG["rsi_weight_uptrend"]
    return CONFIG["rsi_weight_overbought"]


def _vol_tier_score(surge: float) -> float:
    """Replicate the v3.3 volume-surge scoring tiers (config vol_surge_tier*)."""
    if surge >= CONFIG["vol_surge_tier1"]:
        return 1.0
    if surge >= CONFIG["vol_surge_tier2"]:
        return 0.7
    if surge >= CONFIG["vol_surge_tier3"]:
        return 0.4
    return 0.1


def _download_panels(tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Bulk-download daily Close/Volume panels for the snapshot universe."""
    import yfinance as yf

    closes: list[pd.DataFrame] = []
    volumes: list[pd.DataFrame] = []
    symbols = [f"{ticker}.JK" for ticker in tickers]
    chunk_size = 150
    for start in range(0, len(symbols), chunk_size):
        chunk = symbols[start : start + chunk_size]
        raw = yf.download(
            chunk,
            start=_TECH_PANEL_START,
            progress=False,
            auto_adjust=True,
            group_by="column",
            threads=True,
        )
        if raw is None or raw.empty:
            continue
        if not isinstance(raw.columns, pd.MultiIndex):
            # single-ticker chunk degenerates to flat columns
            closes.append(raw[["Close"]].rename(columns={"Close": chunk[0]}))
            volumes.append(raw[["Volume"]].rename(columns={"Volume": chunk[0]}))
            continue
        closes.append(raw["Close"])
        volumes.append(raw["Volume"])
    if not closes:
        raise SystemExit("yfinance returned no data for the snapshot universe")
    close_panel = pd.concat(closes, axis=1)
    volume_panel = pd.concat(volumes, axis=1)
    close_panel.columns = [str(c).replace(".JK", "").upper() for c in close_panel.columns]
    volume_panel.columns = [str(c).replace(".JK", "").upper() for c in volume_panel.columns]
    close_panel = close_panel.loc[:, ~close_panel.columns.duplicated()]
    volume_panel = volume_panel.loc[:, ~volume_panel.columns.duplicated()]
    return close_panel, volume_panel


def _technical_ic(
    frames: list[pd.DataFrame], dates: list[pd.Timestamp | None]
) -> tuple[dict[str, list[float]], int]:
    universe = sorted({t for frame in frames for t in frame["Ticker"] if t and t != "NAN"})
    close_panel, volume_panel = _download_panels(universe)

    rsi_panel = close_panel.apply(compute_rsi)
    vol_avg20 = volume_panel.rolling(20).mean()
    surge_panel = volume_panel / vol_avg20
    mom22_panel = close_panel / close_panel.shift(22) - 1.0
    fwd_panel = close_panel.shift(-_TECH_FORWARD_DAYS) / close_panel - 1.0

    values: dict[str, list[float]] = {name: [] for name in TECHNICAL_SIGNALS}
    usable_periods = 0
    index = close_panel.index
    for frame, snap_date in zip(frames, dates):
        if snap_date is None:
            continue
        eligible = index[index <= snap_date]
        if eligible.empty:
            continue
        bar = eligible[-1]
        cross = pd.DataFrame(
            {
                "rsi14": rsi_panel.loc[bar],
                "vol_surge": surge_panel.loc[bar],
                "price_mom_22d": mom22_panel.loc[bar],
                "forward_return": fwd_panel.loc[bar],
            }
        )
        cross = cross[cross.index.isin(set(frame["Ticker"]))]
        cross["rsi_score"] = cross["rsi14"].map(
            lambda value: _rsi_tier_score(value) if pd.notna(value) else float("nan")
        )
        cross["vol_score"] = cross["vol_surge"].map(
            lambda value: _vol_tier_score(value) if pd.notna(value) else float("nan")
        )
        period_used = False
        for signal in TECHNICAL_SIGNALS:
            ic = _spearman_ic(cross, signal)
            if ic is not None:
                values[signal].append(ic)
                period_used = True
        if period_used:
            usable_periods += 1
    return values, usable_periods


# ── Aggregation & report ──────────────────────────────────────────────────────


def _benjamini_hochberg(rows: list[dict]) -> None:
    ranked = sorted(
        [row for row in rows if row["p_value"] is not None],
        key=lambda row: row["p_value"],
    )
    m = len(ranked)
    prev = 1.0
    for rank, row in reversed(list(enumerate(ranked, start=1))):
        q_value = min(prev, row["p_value"] * m / rank)
        row["bh_q_value"] = round(q_value, 6)
        prev = q_value


def _summarize(values: dict[str, list[float]], family: dict[str, str]) -> list[dict]:
    rows: list[dict] = []
    for signal, ics in values.items():
        series = pd.Series(ics, dtype=float)
        if len(series) < 3 or float(series.std(ddof=1)) <= 1e-12:
            rows.append(
                {
                    "signal": signal,
                    "family": family.get(signal, "?"),
                    "periods": int(len(series)),
                    "mean_ic": None,
                    "t_stat": None,
                    "p_value": None,
                    "bh_q_value": None,
                    "passes_hlz": False,
                    "passes_fdr": False,
                }
            )
            continue
        mean_ic = float(series.mean())
        t_stat = mean_ic / (float(series.std(ddof=1)) / math.sqrt(len(series)))
        p_value = 2.0 * (1.0 - _normal_cdf(abs(t_stat)))
        rows.append(
            {
                "signal": signal,
                "family": family.get(signal, "?"),
                "periods": int(len(series)),
                "mean_ic": round(mean_ic, 6),
                "t_stat": round(t_stat, 6),
                "p_value": round(p_value, 6),
                "bh_q_value": None,
                "passes_hlz": abs(t_stat) >= _HLZ_TSTAT_THRESHOLD and mean_ic > 0.05,
                "passes_fdr": False,
            }
        )
    _benjamini_hochberg(rows)
    for row in rows:
        row["passes_fdr"] = row["bh_q_value"] is not None and row["bh_q_value"] <= 0.05
    return rows


def _build_markdown(payload: dict) -> str:
    lines = [
        "# Screener Signal IC Validation",
        "",
        f"Generated: {payload['generated_at']}",
        f"Snapshots: {payload['snapshot_count']}",
        "Forward horizon: fundamental = next XLSX snapshot; "
        f"technical = {_TECH_FORWARD_DAYS} trading days (yfinance panel)",
        f"HLZ threshold: abs(t-stat) >= {payload['hlz_tstat_threshold']} AND mean IC > 0.05",
        "BH correction applied across the combined signal family.",
        "",
        "| Signal | Family | Periods | Mean IC | t-stat | p-value | BH q-value | HLZ pass | FDR pass |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["signals"]:
        def fmt(value: object) -> str:
            return "-" if value is None else str(value)

        lines.append(
            f"| {row['signal']} | {row['family']} | {row['periods']} | {fmt(row['mean_ic'])} | "
            f"{fmt(row['t_stat'])} | {fmt(row['p_value'])} | {fmt(row['bh_q_value'])} | "
            f"{row['passes_hlz']} | {row['passes_fdr']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output", help="Directory containing IDX XLSX snapshots")
    parser.add_argument(
        "--skip-technical",
        action="store_true",
        help="Skip the yfinance-based RSI/volume IC block (fundamental only)",
    )
    args = parser.parse_args()

    files = _xlsx_files(Path(args.output_dir))
    if len(files) < 4:
        raise SystemExit("Need at least 4 local IDX XLSX snapshots for IC validation")

    frames = [_prepare_frame(path) for path in files]
    dates = [_snapshot_date(path) for path in files]

    values = _fundamental_ic(frames)
    family = {name: "fundamental" for name in FUNDAMENTAL_SIGNALS}
    tech_periods = 0
    if not args.skip_technical:
        tech_values, tech_periods = _technical_ic(frames, dates)
        values.update(tech_values)
        family.update({name: "technical" for name in TECHNICAL_SIGNALS})

    signals = _summarize(values, family)
    payload = {
        "generated_at": date.today().isoformat(),
        "snapshot_count": len(files),
        "snapshots": [path.name for path in files],
        "technical_periods": tech_periods,
        "hlz_tstat_threshold": _HLZ_TSTAT_THRESHOLD,
        "signals": signals,
    }

    out_dir = Path(args.output_dir) / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"screener_signal_ic_{payload['generated_at']}"
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(_build_markdown(payload), encoding="utf-8")
    print(f"Wrote {out_dir / f'{stem}.md'}")


if __name__ == "__main__":
    main()
