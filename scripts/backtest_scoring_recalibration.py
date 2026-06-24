"""Compare legacy vs recalibrated quant scoring on local XLSX snapshots.

This is a snapshot backtest, not a full intraday OHLCV replay. It uses each
historical "IDX Fundamental Analysis YYYY-MM-DD.xlsx" workbook as the ranking
date, then measures forward close-to-close return to the next available workbook.

Why this exists:
- The recalibration changed cross-sectional scoring weights and factor proxies.
- Local workbooks contain point-in-time fundamentals and closing prices.
- yfinance technical replay would add network and data-vendor noise; this keeps
  the comparison focused on old vs new scoring.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.quant_filter.config import CONFIG, FINANCIAL_SECTORS, SECTOR_MEDIAN_PE, SECTOR_PBV_BENCHMARK
from core.quant_filter.pipeline import (
    _build_sector_map,
    _compute_prof_score,
    _compute_val_score,
    _ocf_price_ratio_from_row,
    _row_float,
)


SNAPSHOT_RE = re.compile(r"IDX Fundamental Analysis (\d{4})-(\d{2})-(\d{2})\.xlsx$")


@dataclass
class Snapshot:
    path: str
    as_of: str
    frame: pd.DataFrame


@dataclass
class TradeSample:
    variant: str
    top_n: int
    signal_date: str
    exit_date: str
    holding_days: int
    ticker: str
    sector: str
    score: float
    entry_price: float
    exit_price: float
    return_pct: float


def _parse_snapshot_date(path: Path) -> date | None:
    match = SNAPSHOT_RE.match(path.name)
    if not match:
        return None
    year, month, day = (int(part) for part in match.groups())
    return date(year, month, day)


def _numeric(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "-", "N/A"):
            return default
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _tier(value: float, tier1: float, tier2: float, tier3: float = 0.0) -> float:
    if value >= tier1:
        return 1.0
    if value >= tier2:
        return 0.7
    if value >= tier3:
        return 0.4
    return 0.1


def _price_momentum_component(row: pd.Series, weight: float, cfg: dict) -> float:
    ret_1m = _numeric(
        row.get("1 Month Price Returns")
        if "1 Month Price Returns" in row
        else row.get("price_return_1m")
    )
    if ret_1m >= cfg["price_mom_tier1"]:
        score = 1.0
    elif ret_1m >= cfg["price_mom_tier2"]:
        score = 0.7
    elif ret_1m >= cfg["price_mom_tier3"]:
        score = 0.4
    else:
        score = 0.0
    return weight * score


def _relative_strength_component(row: pd.Series, weight: float) -> float:
    """Use XLSX Relative Strength Rating as a proxy for RSI+volume timing."""
    rs = _numeric(row.get("Relative Strength Rating"))
    if rs > 1.0:
        rs = rs / 100.0
    return weight * max(0.0, min(rs, 1.0))


def _legacy_val_score(row: pd.Series, cfg: dict) -> float:
    sector = row.get("Sector", "default")
    weight = cfg["weight_valuation"]
    if sector in FINANCIAL_SECTORS:
        pbv = _numeric(row.get("Current Price to Book Value"))
        fair_lo = SECTOR_PBV_BENCHMARK.get(sector, SECTOR_PBV_BENCHMARK["default"])["fair_lo"]
        if pbv <= 0:
            return weight * 0.10
        if pbv < fair_lo * 0.70:
            return weight * 1.00
        if pbv < fair_lo * 0.90:
            return weight * 0.70
        if pbv <= fair_lo:
            return weight * 0.40
        return weight * 0.10

    gap = _numeric(row.get("Valuation_Gap_Pct"))
    graham_tier = _tier(gap, cfg["val_tier1_gap"], cfg["val_tier2_gap"], cfg["val_tier3_gap"])
    eps = _numeric(row.get("Current EPS (TTM)"))
    price = _numeric(row.get("Close Price"))
    if eps <= 0 or price <= 0:
        return weight * graham_tier

    current_pe = price / eps
    sector_median_pe = SECTOR_MEDIAN_PE.get(sector, SECTOR_MEDIAN_PE["default"])
    pe_gap_pct = max(0.0, (sector_median_pe - current_pe) / sector_median_pe * 100)
    pe_tier = _tier(pe_gap_pct, cfg["val_tier1_gap"], cfg["val_tier2_gap"], cfg["val_tier3_gap"])
    return weight * (0.70 * graham_tier + 0.30 * pe_tier)


def _legacy_prof_score(row: pd.Series, cfg: dict) -> float:
    roe = _numeric(row.get("Return on Equity (TTM)"))
    if roe <= 0:
        return 0.0
    if roe >= cfg["prof_roe_tier1"]:
        return cfg["weight_profitability"] * 1.0
    if roe >= cfg["prof_roe_tier2"]:
        return cfg["weight_profitability"] * 0.7
    return cfg["weight_profitability"] * 0.4


def _quality_penalty(row: pd.Series, cfg: dict) -> float:
    penalty = 0.0
    piotroski = int(_numeric(row.get("Piotroski F-Score")))
    if piotroski >= cfg["piotroski_strong_min"]:
        penalty += cfg["piotroski_strong_bonus"]
    elif piotroski < cfg["min_piotroski"]:
        penalty += cfg.get("penalty_piotroski_fail", -30)
    elif piotroski <= cfg["piotroski_weak_max"]:
        penalty += cfg["piotroski_weak_penalty"]

    altman = _numeric(row.get("Altman Z-Score (Modified)"))
    if 0 < altman < cfg["min_altman_z"]:
        penalty += cfg.get("penalty_altman_z_fail", -40)

    roe = _numeric(row.get("Return on Equity (TTM)"))
    if roe < cfg["roe_penalty_threshold"]:
        penalty += cfg.get("penalty_roe_fail", -30)

    if _numeric(row.get("Valuation_Gap_Pct")) <= 0:
        penalty -= 10

    return penalty


def _legacy_config() -> dict:
    cfg = dict(CONFIG)
    cfg.update(
        {
            "weight_valuation": 20,
            "weight_profitability": 10,
            "weight_momentum_rsi": 25,
            "weight_momentum_vol": 25,
            "weight_price_momentum": 20,
        }
    )
    return cfg


def _recalibrated_score(row: pd.Series, cfg: dict) -> float:
    score = (
        _compute_val_score(row, cfg)
        + _compute_prof_score(row, cfg)
        + _relative_strength_component(
            row,
            cfg["weight_momentum_rsi"] + cfg["weight_momentum_vol"],
        )
        + _price_momentum_component(row, cfg["weight_price_momentum"], cfg)
        + _quality_penalty(row, cfg)
    )
    return max(0.0, min(float(score), 100.0))


def _legacy_score(row: pd.Series, cfg: dict) -> float:
    score = (
        _legacy_val_score(row, cfg)
        + _legacy_prof_score(row, cfg)
        + _relative_strength_component(
            row,
            cfg["weight_momentum_rsi"] + cfg["weight_momentum_vol"],
        )
        + _price_momentum_component(row, cfg["weight_price_momentum"], cfg)
        + _quality_penalty(row, cfg)
    )
    return max(0.0, min(float(score), 100.0))


def _load_snapshot(path: Path, cfg: dict) -> Snapshot:
    as_of = _parse_snapshot_date(path)
    if as_of is None:
        raise ValueError(f"Cannot parse snapshot date from {path.name}")

    df_ks = pd.read_excel(path, sheet_name="key-statistics")
    df_prices = pd.read_excel(path, sheet_name="stock-prices")
    df_anal = pd.read_excel(path, sheet_name="analysis")
    df_idx = pd.read_excel(path, sheet_name="idx-stocks")

    df = (
        df_ks.merge(
            df_prices[["Ticker", "Close Price", "Volume", "High Price", "Low Price"]],
            on="Ticker",
            how="left",
        )
        .merge(
            df_anal[["Ticker", "Price to Equity Discount (%)", "Composite Rank"]],
            on="Ticker",
            how="left",
        )
        .merge(df_idx[["Ticker", "Name", "Note"]], on="Ticker", how="left")
    )

    numeric_columns = [
        "Close Price",
        "Debt to Equity Ratio (Quarter)",
        "Current Price to Book Value",
        "Return on Equity (TTM)",
        "Return on Assets (TTM)",
        "Current EPS (TTM)",
        "Piotroski F-Score",
        "Altman Z-Score (Modified)",
        "Current Book Value Per Share",
        "Current Share Outstanding",
        "Cash From Operations (TTM)",
        "Cash Flow from Operations (TTM)",
        "Operating Cash Flow (TTM)",
        "1 Month Price Returns",
        "Relative Strength Rating",
    ]
    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if cfg.get("exclude_pemantauan", True):
        df = df[~df["Note"].astype(str).str.contains("PEMANTAUAN KHUSUS", na=False)].copy()

    names_map = dict(zip(df_idx["Ticker"], df_idx["Name"].fillna("")))
    sector_map = _build_sector_map(
        tickers=df["Ticker"].tolist(),
        names=names_map,
        cache_file=cfg["sector_cache_file"],
        logger=_NoopLogger(),
    )
    df["Sector"] = df["Ticker"].map(sector_map).fillna("default")
    df["PBV_Sector_Pctile"] = df.groupby("Sector")["Current Price to Book Value"].rank(
        pct=True,
        ascending=True,
    )
    max_der_map = cfg["max_der_by_sector"]
    df["Max_DER_Allowed"] = df["Sector"].map(max_der_map).fillna(max_der_map["default"])

    filtered = df[
        (df["Close Price"] > cfg["min_close_price"])
        & (df["Debt to Equity Ratio (Quarter)"] <= df["Max_DER_Allowed"])
        & (df["PBV_Sector_Pctile"] < cfg["pbv_sector_pctile"])
        & (df["Current Price to Book Value"] < cfg["max_pbv_hard"])
    ].copy()

    eps = filtered["Current EPS (TTM)"]
    bvps = filtered["Current Book Value Per Share"]
    valid_graham = (eps > 0) & (bvps > 0)
    filtered["Graham_Number"] = np.where(
        valid_graham,
        np.sqrt(np.clip(cfg["graham_k"] * eps * bvps, 0, None)),
        0,
    )
    fv_cap = filtered["Close Price"] * cfg["graham_fv_cap_multiplier"]
    filtered["Graham_Number"] = np.where(
        filtered["Graham_Number"] > fv_cap,
        fv_cap,
        filtered["Graham_Number"],
    )
    low_roe_mask = filtered["Return on Equity (TTM)"] < cfg["roe_penalty_threshold"]
    low_roe_cap = filtered["Close Price"] * cfg["graham_low_roe_cap_mult"]
    filtered["Graham_Number"] = np.where(
        low_roe_mask & (filtered["Graham_Number"] > low_roe_cap),
        low_roe_cap,
        filtered["Graham_Number"],
    )
    filtered["Valuation_Gap_Pct"] = (
        (filtered["Graham_Number"] - filtered["Close Price"])
        / filtered["Close Price"]
        * 100
    )
    filtered["OCF_Price_Ratio"] = filtered.apply(_ocf_price_ratio_from_row, axis=1)
    filtered["RNOA_Estimate"] = filtered.apply(
        lambda row: _row_float(row, "RNOA", "Return on Net Operating Assets")
        or _row_float(row, "Return on Assets (TTM)", "ROA (TTM)", "ROA"),
        axis=1,
    )

    legacy_cfg = _legacy_config()
    filtered["legacy_score"] = filtered.apply(lambda row: _legacy_score(row, legacy_cfg), axis=1)
    filtered["recalibrated_score"] = filtered.apply(lambda row: _recalibrated_score(row, cfg), axis=1)
    filtered["ocf_available"] = filtered["OCF_Price_Ratio"] > 0
    filtered["rnoa_roa_available"] = filtered["RNOA_Estimate"] > 0
    return Snapshot(str(path), as_of.isoformat(), filtered)


class _NoopLogger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _collect_trades(
    snapshots: list[Snapshot],
    *,
    top_ns: list[int],
) -> tuple[list[TradeSample], list[dict[str, Any]]]:
    trades: list[TradeSample] = []
    paired_rows: list[dict[str, Any]] = []
    for current, nxt in zip(snapshots, snapshots[1:]):
        exit_prices = nxt.frame.set_index("Ticker")["Close Price"].to_dict()
        current_date = date.fromisoformat(current.as_of)
        next_date = date.fromisoformat(nxt.as_of)
        holding_days = (next_date - current_date).days
        for top_n in top_ns:
            buckets: dict[str, list[TradeSample]] = {}
            for variant, score_col in (
                ("legacy", "legacy_score"),
                ("recalibrated", "recalibrated_score"),
            ):
                ranked = current.frame.sort_values(score_col, ascending=False).head(top_n)
                samples: list[TradeSample] = []
                for _, row in ranked.iterrows():
                    ticker = str(row["Ticker"]).upper()
                    entry = _numeric(row.get("Close Price"))
                    exit_price = _numeric(exit_prices.get(ticker), default=math.nan)
                    if not (entry > 0 and exit_price > 0 and not math.isnan(exit_price)):
                        continue
                    sample = TradeSample(
                        variant=variant,
                        top_n=top_n,
                        signal_date=current.as_of,
                        exit_date=nxt.as_of,
                        holding_days=holding_days,
                        ticker=ticker,
                        sector=str(row.get("Sector") or "default"),
                        score=round(_numeric(row.get(score_col)), 4),
                        entry_price=entry,
                        exit_price=exit_price,
                        return_pct=round((exit_price - entry) / entry * 100.0, 4),
                    )
                    samples.append(sample)
                    trades.append(sample)
                buckets[variant] = samples

            legacy_avg = _mean([s.return_pct for s in buckets.get("legacy", [])])
            recal_avg = _mean([s.return_pct for s in buckets.get("recalibrated", [])])
            legacy_tickers = {s.ticker for s in buckets.get("legacy", [])}
            recal_tickers = {s.ticker for s in buckets.get("recalibrated", [])}
            union = legacy_tickers | recal_tickers
            paired_rows.append(
                {
                    "signal_date": current.as_of,
                    "exit_date": nxt.as_of,
                    "holding_days": holding_days,
                    "top_n": top_n,
                    "legacy_avg_return_pct": legacy_avg,
                    "recalibrated_avg_return_pct": recal_avg,
                    "delta_pct_points": (
                        round(recal_avg - legacy_avg, 4)
                        if legacy_avg is not None and recal_avg is not None
                        else None
                    ),
                    "overlap_count": len(legacy_tickers & recal_tickers),
                    "overlap_ratio": round(len(legacy_tickers & recal_tickers) / len(union), 4)
                    if union
                    else None,
                }
            )
    return trades, paired_rows


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 4) if values else None


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 4) if values else None


def _summarize(samples: list[TradeSample]) -> dict[str, Any]:
    returns = [sample.return_pct for sample in samples]
    wins = [value for value in returns if value > 0]
    hit_5 = [value for value in returns if value >= 5.0]
    loss_5 = [value for value in returns if value <= -5.0]
    days = [sample.holding_days for sample in samples]
    scores = [sample.score for sample in samples]
    return {
        "signals": len(samples),
        "avg_return_pct": _mean(returns),
        "median_return_pct": _median(returns),
        "win_rate": round(len(wins) / len(returns), 4) if returns else None,
        "hit_plus_5pct_rate": round(len(hit_5) / len(returns), 4) if returns else None,
        "loss_minus_5pct_rate": round(len(loss_5) / len(returns), 4) if returns else None,
        "avg_holding_days": _mean(days),
        "avg_score": _mean(scores),
        "best": max(returns) if returns else None,
        "worst": min(returns) if returns else None,
    }


def _build_report(result: dict[str, Any]) -> str:
    lines = [
        "# Scoring Recalibration Snapshot Backtest",
        "",
        f"Generated: {result['generated_at']}",
        f"Snapshots: {result['snapshot_count']} ({result['first_snapshot']} to {result['last_snapshot']})",
        "",
        "## Method",
        "",
        "- Universe: local `output/IDX Fundamental Analysis YYYY-MM-DD.xlsx` snapshots.",
        "- Entry price: snapshot close price.",
        "- Exit price: next available snapshot close price.",
        "- Legacy score: 20 value, 10 profitability, 70 momentum proxy.",
        "- Recalibrated score: 40 value, 30 quality, 30 momentum proxy.",
        "- Momentum timing proxy: XLSX `Relative Strength Rating` plus `1 Month Price Returns`.",
        "- This isolates scoring/ranking. It does not replay intraday target/stop fills.",
        "",
        "## Aggregate Results",
        "",
        "| Top N | Variant | Signals | Avg Return | Median | Win Rate | +5% Hit | -5% Loss | Avg Score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["aggregate_table"]:
        lines.append(
            "| {top_n} | {variant} | {signals} | {avg_return_pct} | {median_return_pct} | "
            "{win_rate} | {hit_plus_5pct_rate} | {loss_minus_5pct_rate} | {avg_score} |".format(
                **row
            )
        )
    lines += [
        "",
        "## Paired Snapshot Delta",
        "",
        "| Top N | Periods | Avg Delta pp | Median Delta pp | Recalibrated Better | Avg Overlap |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in result["paired_summary"]:
        lines.append(
            "| {top_n} | {periods} | {avg_delta_pct_points} | {median_delta_pct_points} | "
            "{recalibrated_better_rate} | {avg_overlap_ratio} |".format(**row)
        )
    lines += [
        "",
        "## Latest Snapshot Top 10",
        "",
        "| Variant | Rank | Ticker | Sector | Score | OCF/Price | RNOA/ROA Proxy |",
        "|---|---:|---|---|---:|---:|---:|",
    ]
    for row in result["latest_top10"]:
        lines.append(
            "| {variant} | {rank} | {ticker} | {sector} | {score} | {ocf_price_ratio} | "
            "{rnoa_roa_proxy} |".format(**row)
        )
    lines += [
        "",
        "## Caveats",
        "",
        "- This is close-to-next-snapshot forward return, not a full trade simulator.",
        "- RSI, volume surge, MA20/MA200, ATR, ex-date, and risk-governor gates are not replayed.",
        "- The comparison is still useful for the recalibration question because both variants see the same local snapshots and differ only in score construction.",
    ]
    return "\n".join(lines) + "\n"


def run_backtest(
    *,
    output_dir: Path,
    top_ns: list[int],
    generated_at: str,
) -> dict[str, Any]:
    paths = sorted(
        path for path in output_dir.glob("IDX Fundamental Analysis *.xlsx") if _parse_snapshot_date(path)
    )
    cfg = dict(CONFIG)
    snapshots = [_load_snapshot(path, cfg) for path in paths]
    if len(snapshots) < 2:
        raise RuntimeError("Need at least two snapshots to compute forward returns.")

    trades, paired_rows = _collect_trades(snapshots, top_ns=top_ns)
    aggregate_table: list[dict[str, Any]] = []
    for top_n in top_ns:
        for variant in ("legacy", "recalibrated"):
            samples = [s for s in trades if s.top_n == top_n and s.variant == variant]
            row = {"top_n": top_n, "variant": variant, **_summarize(samples)}
            aggregate_table.append(row)

    paired_summary: list[dict[str, Any]] = []
    for top_n in top_ns:
        rows = [row for row in paired_rows if row["top_n"] == top_n]
        deltas = [row["delta_pct_points"] for row in rows if row["delta_pct_points"] is not None]
        overlaps = [row["overlap_ratio"] for row in rows if row["overlap_ratio"] is not None]
        paired_summary.append(
            {
                "top_n": top_n,
                "periods": len(rows),
                "avg_delta_pct_points": _mean(deltas),
                "median_delta_pct_points": _median(deltas),
                "recalibrated_better_rate": round(
                    sum(1 for delta in deltas if delta > 0) / len(deltas),
                    4,
                )
                if deltas
                else None,
                "avg_overlap_ratio": _mean(overlaps),
            }
        )

    latest = snapshots[-1].frame
    latest_top10: list[dict[str, Any]] = []
    for variant, score_col in (("legacy", "legacy_score"), ("recalibrated", "recalibrated_score")):
        ranked = latest.sort_values(score_col, ascending=False).head(10).reset_index(drop=True)
        for idx, row in ranked.iterrows():
            latest_top10.append(
                {
                    "variant": variant,
                    "rank": idx + 1,
                    "ticker": str(row["Ticker"]).upper(),
                    "sector": str(row.get("Sector") or "default"),
                    "score": round(_numeric(row.get(score_col)), 2),
                    "ocf_price_ratio": round(_numeric(row.get("OCF_Price_Ratio")) * 100, 2),
                    "rnoa_roa_proxy": round(_numeric(row.get("RNOA_Estimate")) * 100, 2),
                }
            )

    ocf_coverage = [
        round(float(snapshot.frame["ocf_available"].mean()), 4) if len(snapshot.frame) else 0.0
        for snapshot in snapshots
    ]
    rnoa_roa_coverage = [
        round(float(snapshot.frame["rnoa_roa_available"].mean()), 4) if len(snapshot.frame) else 0.0
        for snapshot in snapshots
    ]

    return {
        "generated_at": generated_at,
        "snapshot_count": len(snapshots),
        "first_snapshot": snapshots[0].as_of,
        "last_snapshot": snapshots[-1].as_of,
        "top_ns": top_ns,
        "coverage": {
            "avg_ocf_price_available": _mean(ocf_coverage),
            "avg_rnoa_or_roa_available": _mean(rnoa_roa_coverage),
        },
        "aggregate_table": aggregate_table,
        "paired_summary": paired_summary,
        "paired_periods": paired_rows,
        "latest_top10": latest_top10,
        "trades": [asdict(sample) for sample in trades],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest legacy vs recalibrated snapshot scoring.")
    parser.add_argument("--output-dir", default="output", type=Path)
    parser.add_argument("--top-n", default="10,20,50")
    parser.add_argument("--generated-at", default=date.today().isoformat())
    parser.add_argument("--json-output", default="output/research/scoring_recalibration_backtest_2026-06-23.json", type=Path)
    parser.add_argument("--md-output", default="output/research/scoring_recalibration_backtest_2026-06-23.md", type=Path)
    args = parser.parse_args()

    top_ns = [int(part) for part in str(args.top_n).split(",") if part.strip()]
    result = run_backtest(
        output_dir=args.output_dir,
        top_ns=top_ns,
        generated_at=args.generated_at,
    )
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    args.md_output.parent.mkdir(parents=True, exist_ok=True)
    args.md_output.write_text(_build_report(result), encoding="utf-8")

    print(json.dumps({
        "json_output": str(args.json_output),
        "md_output": str(args.md_output),
        "aggregate_table": result["aggregate_table"],
        "paired_summary": result["paired_summary"],
        "coverage": result["coverage"],
    }, indent=2))


if __name__ == "__main__":
    main()
