"""Validate IDX OCF/Price distribution by sector from local XLSX snapshots."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quant_filter.config import CONFIG, TICKER_SECTOR_HARDCODE  # noqa: E402
from core.quant_filter.pipeline import _ocf_price_ratio_from_row  # noqa: E402

_OCF_SOURCE_COLUMNS = {
    "OCF/Price",
    "OCF_Price_Ratio",
    "Operating Cash Flow Yield",
    "Operating Cash Flow Per Share",
    "OCF Per Share",
    "Cash Flow from Operations Per Share",
    "Operating Cash Flow (TTM)",
    "Cash From Operations (TTM)",
    "Cash From Operations",
    "Cash Flow from Operations (TTM)",
    "Cash Flow from Operations",
    "Operating Cash Flow",
    "CFO (TTM)",
    "Current Share Outstanding",
    "Shares Outstanding",
    "shares_outstanding",
}


def _xlsx_files(output_dir: Path) -> list[Path]:
    patterns = [
        "IDX Fundamental Analysis *.xlsx",
        "IDX_Fundamental_Analysis_*.xlsx",
    ]
    files: list[Path] = []
    for pattern in patterns:
        files.extend(output_dir.glob(pattern))
    return sorted(set(files), key=lambda path: path.name)


def _load_sector_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, str] = {}
    for ticker, value in raw.items():
        key = str(ticker).replace(".JK", "").upper()
        if isinstance(value, dict):
            result[key] = str(value.get("sector", "default"))
        else:
            result[key] = str(value)
    return result


def _prepare_frame(path: Path, sector_cache: dict[str, str]) -> pd.DataFrame:
    frame = pd.read_excel(path)
    frame["Ticker"] = frame["Ticker"].astype(str).str.replace(".JK", "", regex=False).str.upper()
    if "Sector" not in frame.columns:
        frame["Sector"] = frame["Ticker"].map(sector_cache).fillna(
            frame["Ticker"].map(TICKER_SECTOR_HARDCODE)
        )
    frame["Sector"] = frame["Sector"].fillna("default")
    frame["OCF_Price_Ratio"] = frame.apply(_ocf_price_ratio_from_row, axis=1)
    frame["snapshot"] = path.stem
    return frame[["snapshot", "Ticker", "Sector", "OCF_Price_Ratio"]]


def _detected_source_columns(files: list[Path]) -> list[str]:
    found: set[str] = set()
    for path in files:
        try:
            columns = set(pd.read_excel(path, nrows=0).columns)
        except Exception:
            continue
        found.update(columns & _OCF_SOURCE_COLUMNS)
    return sorted(found)


def _sector_stats(frame: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    for sector, group in frame.groupby("Sector"):
        values = pd.to_numeric(group["OCF_Price_Ratio"], errors="coerce")
        positive = values[values > 0]
        rows.append(
            {
                "sector": sector,
                "rows": int(len(group)),
                "available": int(len(positive)),
                "coverage": round(float(len(positive) / len(group)), 4) if len(group) else 0.0,
                "non_positive_or_missing": int(len(group) - len(positive)),
                "outlier_gt_50pct": int((positive > 0.50).sum()),
                "mean": round(float(positive.mean()), 4) if len(positive) else None,
                "p10": round(float(positive.quantile(0.10)), 4) if len(positive) else None,
                "p25": round(float(positive.quantile(0.25)), 4) if len(positive) else None,
                "median": round(float(positive.median()), 4) if len(positive) else None,
                "p75": round(float(positive.quantile(0.75)), 4) if len(positive) else None,
                "p90": round(float(positive.quantile(0.90)), 4) if len(positive) else None,
            }
        )
    return sorted(rows, key=lambda item: item["sector"])


def _build_markdown(payload: dict) -> str:
    lines = [
        "# OCF/Price Sector Distribution Validation",
        "",
        f"Generated: {payload['generated_at']}",
        f"Snapshots: {payload['snapshot_count']}",
        f"Rows: {payload['row_count']}",
        f"Detected OCF source columns: {', '.join(payload['source_columns']) or 'none'}",
        "",
        "| Sector | Rows | Available | Coverage | Mean | P10 | P25 | Median | P75 | P90 | Outlier >50% |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in payload["sectors"]:
        def fmt(value: float | int | None, pct: bool = False) -> str:
            if value is None:
                return "-"
            return f"{value:.1%}" if pct else str(value)

        lines.append(
            "| {sector} | {rows} | {available} | {coverage} | {mean} | {p10} | {p25} | "
            "{median} | {p75} | {p90} | {outlier} |".format(
                sector=row["sector"],
                rows=row["rows"],
                available=row["available"],
                coverage=fmt(row["coverage"], pct=True),
                mean=fmt(row["mean"], pct=True),
                p10=fmt(row["p10"], pct=True),
                p25=fmt(row["p25"], pct=True),
                median=fmt(row["median"], pct=True),
                p75=fmt(row["p75"], pct=True),
                p90=fmt(row["p90"], pct=True),
                outlier=row["outlier_gt_50pct"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", default="output", help="Directory containing IDX XLSX snapshots")
    parser.add_argument("--latest-only", action="store_true", help="Use only the newest local snapshot")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    files = _xlsx_files(output_dir)
    if not files:
        raise SystemExit(f"No IDX fundamental XLSX snapshots found in {output_dir}")
    if args.latest_only:
        files = [files[-1]]

    sector_cache = _load_sector_cache(Path(CONFIG["sector_cache_file"]))
    frame = pd.concat([_prepare_frame(path, sector_cache) for path in files], ignore_index=True)
    payload = {
        "generated_at": date.today().isoformat(),
        "snapshot_count": len(files),
        "snapshots": [path.name for path in files],
        "source_columns": _detected_source_columns(files),
        "row_count": int(len(frame)),
        "sectors": _sector_stats(frame),
    }

    out_dir = output_dir / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"ocf_price_sector_distribution_{payload['generated_at']}"
    (out_dir / f"{stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (out_dir / f"{stem}.md").write_text(_build_markdown(payload), encoding="utf-8")
    print(f"Wrote {out_dir / f'{stem}.md'}")


if __name__ == "__main__":
    main()
