"""Run screener signal IC tests with multiple-testing correction on local snapshots."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quant_filter.pipeline import _ocf_price_ratio_from_row, _row_float  # noqa: E402

_HLZ_TSTAT_THRESHOLD = 2.57


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _xlsx_files(output_dir: Path) -> list[Path]:
    files: list[Path] = []
    for pattern in ("IDX Fundamental Analysis *.xlsx", "IDX_Fundamental_Analysis_*.xlsx"):
        files.extend(output_dir.glob(pattern))
    return sorted(set(files), key=lambda path: path.name)


def _numeric_col(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(float("nan"), index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _prepare_frame(path: Path) -> pd.DataFrame:
    frame = pd.read_excel(path)
    frame["Ticker"] = frame["Ticker"].astype(str).str.replace(".JK", "", regex=False).str.upper()
    frame["Close Price"] = _numeric_col(frame, "Close Price")
    frame["ocf_price"] = frame.apply(_ocf_price_ratio_from_row, axis=1)
    frame["rnoa_roa"] = frame.apply(
        lambda row: _row_float(row, "RNOA", "Return on Net Operating Assets")
        or _row_float(row, "Return on Assets (TTM)", "ROA (TTM)", "ROA"),
        axis=1,
    )
    frame["roe"] = _numeric_col(frame, "Return on Equity (TTM)")
    frame["pbv_inverse"] = -_numeric_col(frame, "Current Price to Book Value")
    eps = _numeric_col(frame, "Current EPS (TTM)")
    price = frame["Close Price"]
    frame["pe_inverse"] = -(price / eps.where(eps > 0))
    frame["price_to_equity_discount"] = _numeric_col(frame, "Price to Equity Discount (%)")
    return frame[
        [
            "Ticker",
            "Close Price",
            "ocf_price",
            "rnoa_roa",
            "roe",
            "pbv_inverse",
            "pe_inverse",
            "price_to_equity_discount",
        ]
    ].copy()


def _ic_by_period(frames: list[pd.DataFrame]) -> dict[str, list[float]]:
    signal_names = [
        "ocf_price",
        "rnoa_roa",
        "roe",
        "pbv_inverse",
        "pe_inverse",
        "price_to_equity_discount",
    ]
    values: dict[str, list[float]] = {name: [] for name in signal_names}
    for current, nxt in zip(frames, frames[1:]):
        forward = nxt[["Ticker", "Close Price"]].rename(columns={"Close Price": "next_close"})
        merged = current.merge(forward, on="Ticker", how="inner")
        merged["forward_return"] = (merged["next_close"] / merged["Close Price"]) - 1.0
        for signal in signal_names:
            subset = merged[[signal, "forward_return"]].replace([math.inf, -math.inf], pd.NA).dropna()
            if len(subset) < 8:
                continue
            if subset[signal].nunique() < 2 or subset["forward_return"].nunique() < 2:
                continue
            ic = subset[signal].corr(subset["forward_return"], method="spearman")
            if ic is not None and math.isfinite(float(ic)):
                values[signal].append(float(ic))
    return values


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


def _summarize(values: dict[str, list[float]]) -> list[dict]:
    rows: list[dict] = []
    for signal, ics in values.items():
        series = pd.Series(ics, dtype=float)
        if len(series) < 3 or float(series.std(ddof=1)) <= 1e-12:
            rows.append(
                {
                    "signal": signal,
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
        "Forward horizon: next available XLSX snapshot",
        f"HLZ threshold: abs(t-stat) >= {payload['hlz_tstat_threshold']}",
        "",
        "| Signal | Periods | Mean IC | t-stat | p-value | BH q-value | HLZ pass | FDR pass |",
        "|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in payload["signals"]:
        def fmt(value: object) -> str:
            return "-" if value is None else str(value)

        lines.append(
            f"| {row['signal']} | {row['periods']} | {fmt(row['mean_ic'])} | "
            f"{fmt(row['t_stat'])} | {fmt(row['p_value'])} | {fmt(row['bh_q_value'])} | "
            f"{row['passes_hlz']} | {row['passes_fdr']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output", help="Directory containing IDX XLSX snapshots")
    args = parser.parse_args()

    files = _xlsx_files(Path(args.output_dir))
    if len(files) < 4:
        raise SystemExit("Need at least 4 local IDX XLSX snapshots for IC validation")

    frames = [_prepare_frame(path) for path in files]
    signals = _summarize(_ic_by_period(frames))
    payload = {
        "generated_at": date.today().isoformat(),
        "snapshot_count": len(files),
        "snapshots": [path.name for path in files],
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
