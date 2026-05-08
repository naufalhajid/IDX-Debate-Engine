"""Markdown reporting for the IHSG quantitative filter."""

from datetime import datetime

import pandas as pd

from utils.exdate_scanner import ExDateInfo, format_exdate_block

def _build_markdown_report(final_df: pd.DataFrame, cfg: dict) -> str:
    def _float_or_none(row: pd.Series, key: str) -> float | None:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _format_signed_pct(row: pd.Series, key: str) -> str:
        value = _float_or_none(row, key)
        return "N/A" if value is None else f"{value:+.1f}%"

    def _format_ema20(row: pd.Series) -> str:
        ema20 = _float_or_none(row, "ema20")
        price = _float_or_none(row, "Current Price")
        if ema20 is None or price is None:
            return "N/A"
        icon = "✅" if price > ema20 else "⚠️"
        return f"{icon} {ema20:,.2f}"

    def _format_ema20_signal(row: pd.Series) -> str:
        ema20 = _float_or_none(row, "ema20")
        price = _float_or_none(row, "Current Price")
        if ema20 is None or price is None:
            return "N/A"
        return "✅ Uptrend" if price > ema20 else "⚠️ Weak"

    def _format_vol_surge(row: pd.Series) -> str:
        ratio = _float_or_none(row, "vol_surge_ratio")
        if ratio is None:
            return "N/A"
        if ratio >= 2.0:
            icon = "🔥"
        elif ratio >= 1.5:
            icon = "📈"
        elif ratio >= 1.1:
            icon = "➡️"
        else:
            icon = "😴"
        return f"{icon} {ratio:.2f}x"

    def _format_price_mom(row: pd.Series) -> str:
        value = _float_or_none(row, "price_return_1m")
        if value is None:
            return "N/A"
        if value >= 10:
            icon = "🚀"
        elif value >= 5:
            icon = "📈"
        elif value >= 0:
            icon = "➡️"
        else:
            icon = "🔻"
        return f"{icon} {value:+.1f}%"

    def _format_rs_vs_ihsg(row: pd.Series) -> str:
        value = _float_or_none(row, "rs_vs_ihsg_1m")
        if value is None:
            return "N/A"
        if value >= 5:
            icon = "💪"
        elif value >= 0:
            icon = "✅"
        else:
            icon = "⚠️"
        return f"{icon} {value:+.1f}%"

    lines = []
    lines.append(f"# 🏆 Top {cfg['top_n']} High-Conviction IHSG Swing Candidates")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("*Engine: v3.1 — Absolute scoring | Asymmetric RSI | Piotroski integrated | Bank-aware valuation*")
    lines.append(f"**Filter Version:** {cfg.get('version', 'v3.2')} — Swing Trade Optimized")
    lines.append("")
    lines.append(
        "| Rank | Ticker | Sektor | Harga | Stop Loss | Graham Fair Value "
        "| Score | Gap | RSI (14) | Price Mom 1M | RS vs IHSG | PBV | F-Score | Entry Note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")

    for i, (_, r) in enumerate(final_df.iterrows(), 1):
        fv_str = (
            f"Rp {r['Graham_Bear']:,.0f} – Rp {r['Graham_Bull']:,.0f}"
            if r["Est. Fair Value (Graham)"] > 0 else "N/A"
        )
        exdate_icon = " ⚠️" if r["ExDate Risk"] == "WARNING" else ""
        ex_src = f" [{r.get('ExDate Source','?')}]" if r.get("ExDate Source") else ""
        piotroski_icon = (
            "🟢" if r.get("Piotroski F-Score", 0) >= 7 else
            "🟡" if r.get("Piotroski F-Score", 0) >= 4 else "🔴"
        )
        lines.append(
            f"| {i} "
            f"| **{r['Ticker']}**{exdate_icon} "
            f"| {r['Sektor']} "
            f"| Rp {r['Current Price']:,.0f} "
            f"| **Rp {r['Stop Loss Level']:,.0f}** "
            f"| {fv_str} "
            f"| **{r['Composite Score']:.1f}/100** "
            f"| +{r['Valuation Gap (%)']:.1f}% "
            f"| {r['RSI (14)']:.1f} "
            f"| {_format_signed_pct(r, 'price_return_1m')} "
            f"| {_format_signed_pct(r, 'rs_vs_ihsg_1m')} "
            f"| {r['PBV']:.1f}× ({r['PBV vs Sektor']}) "
            f"| {piotroski_icon} {r.get('Piotroski F-Score', 'N/A')}/9 "
            f"| {r['Entry Strategy']}{ex_src} |"
        )

    lines.append("")
    v32_cols = {"price_return_1m", "rs_vs_ihsg_1m", "vol_surge_ratio", "ema20"}
    if not final_df.empty and v32_cols.issubset(final_df.columns):
        lines.append("## 📊 Momentum Snapshot (Sort: Price Mom 1M)")
        lines.append("")
        lines.append("| # | Ticker | Price Mom 1M | RS vs IHSG | Vol Surge | EMA20 Signal |")
        lines.append("|---|--------|-------------|------------|-----------|--------------|")
        momentum_df = final_df.sort_values("price_return_1m", ascending=False)
        for i, (_, mr) in enumerate(momentum_df.iterrows(), 1):
            lines.append(
                f"| {i} "
                f"| {mr['Ticker']} "
                f"| {_format_signed_pct(mr, 'price_return_1m')} "
                f"| {_format_signed_pct(mr, 'rs_vs_ihsg_1m')} "
                f"| {_format_vol_surge(mr)} "
                f"| {_format_ema20_signal(mr)} |"
            )
        lines.append("")

    lines.append("---")
    lines.append("> ⚠️ = Mendekati ex-date dividen. F-Score: 🟢 ≥7 / 🟡 4–6 / 🔴 <4")
    lines.append("")

    # ExDate Detail Blocks untuk WARNING tier
    if not final_df.empty:
        warning_rows = final_df[final_df["ExDate Risk"] == "WARNING"]
        if not warning_rows.empty:
            lines.append("## ⚠️ Dividend Ex-Date Risk Details")
            lines.append("")
            for _, wr in warning_rows.iterrows():
                ex_info: ExDateInfo = wr["_exdate_info"]
                lines.append("```")
                lines.append(format_exdate_block(wr["Ticker"], ex_info).strip())
                lines.append("```")
                lines.append("")

    # Investment thesis untuk rank #1
    if not final_df.empty:
        top1 = final_df.iloc[0]
        max_dd = ((top1["Current Price"] - top1["Stop Loss Level"]) / top1["Current Price"]) * 100
        lines.append(f"## 💡 Investment Thesis: {top1['Ticker']} (Rank #1)")
        lines.append("")
        lines.append(
            f"**{top1['Ticker']}** ({top1['Sektor']}) adalah kandidat tertinggi "
            f"berdasarkan multi-factor swing strategy."
        )
        lines.append("")
        lines.append(
            f"- **Valuation MoS**: Diskon **{top1['Valuation Gap (%)']:.1f}%** "
            f"terhadap Graham Fair Value. "
            f"PBV saat ini {top1['PBV']:.1f}× — **{top1['PBV vs Sektor']}** vs sektor."
        )
        lines.append(
            f"- **Quality**: Piotroski F-Score **{top1.get('Piotroski F-Score','N/A')}/9** | "
            f"Altman Z **{top1.get('Altman Z-Score', 'N/A')}**"
        )
        lines.append(
            f"- **Momentum**: Harga Rp {top1['Current Price']:,.0f} di atas "
            f"SMA-20 (Rp {top1['SMA 20']:,.0f}). {top1['Entry Strategy']}."
        )
        lines.append(
            f"- **Teknikal:** EMA20 `{_format_ema20(top1)}` | "
            f"Vol Surge `{_format_vol_surge(top1)}` | "
            f"Mom 1M `{_format_price_mom(top1)}` | "
            f"RS vs IHSG `{_format_rs_vs_ihsg(top1)}`"
        )
        lines.append(
            f"- **Profitabilitas**: ROE {top1['ROE (TTM)']*100:.1f}% | "
            f"DER {top1['DER (Quarter)']:.2f}×"
        )
        lines.append(
            f"- **Risk Management**: Stop loss di **Rp {top1['Stop Loss Level']:,.0f}** "
            f"(ATR-based, max drawdown ~{max_dd:.1f}%)"
        )
    else:
        lines.append(
            "> Tidak ada ticker yang lolos semua filter. "
            "Coba longgarkan threshold atau perbarui data input."
        )

    return "\n".join(lines)


def _build_position_summary(sizing_result: dict | None) -> str:
    """Build a markdown position-sizing section for the final orchestrator report."""
    if not sizing_result:
        return ""

    positions = sizing_result.get("positions") or []
    summary = sizing_result.get("summary") or {}

    def _rupiah(value: float | int | None) -> str:
        try:
            return "Rp " + f"{float(value):,.0f}".replace(",", ".")
        except (TypeError, ValueError):
            return "Rp 0"

    def _int_id(value: float | int | None) -> str:
        try:
            return f"{int(value):,}".replace(",", ".")
        except (TypeError, ValueError):
            return "0"

    def _pct(value: float | int | None) -> str:
        try:
            return f"{float(value) * 100:.1f}%"
        except (TypeError, ValueError):
            return "0.0%"

    lines = [
        "## 💼 Rekomendasi Posisi",
        "",
        "| # | Ticker | Rating | Lot | Saham | Nilai Posisi | Alokasi | Max Loss | Est. Biaya |",
        "|---|--------|--------|-----|-------|-------------|---------|----------|------------|",
    ]

    for i, position in enumerate(positions, 1):
        lines.append(
            f"| {i} "
            f"| {position.get('ticker', 'N/A')} "
            f"| {position.get('rating', 'N/A')} "
            f"| {_int_id(position.get('lot'))} "
            f"| {_int_id(position.get('shares'))} "
            f"| {_rupiah(position.get('position_value'))} "
            f"| {_pct(position.get('allocation_pct'))} "
            f"| {_rupiah(position.get('max_loss_rp'))} "
            f"| {_rupiah(position.get('total_cost_est'))} |"
        )

    lines += [
        "",
        "---",
        "",
        "### 📊 Portfolio Summary",
        "",
        "| Item | Nilai |",
        "|------|-------|",
        f"| Total Modal | {_rupiah(summary.get('total_capital'))} |",
        f"| Total Deployed | {_rupiah(summary.get('total_deployed'))} |",
        f"| Sisa Cash | {_rupiah(summary.get('remaining_cash'))} |",
        f"| % Deployed | {_pct(summary.get('deployed_pct'))} |",
        f"| Jumlah Posisi | {_int_id(summary.get('total_positions'))} |",
        f"| Est. Total Biaya Transaksi | {_rupiah(summary.get('total_cost_est'))} |",
    ]

    reasoning = sizing_result.get("allocation_reasoning") or {}
    if reasoning:
        risk_factors = reasoning.get("risk_factors_limiting") or []
        lines += [
            "",
            "### Allocation Reasoning",
            "",
            "| Item | Nilai |",
            "|------|-------|",
            f"| Target Deployment | {float(reasoning.get('target_deployment_pct', 0)):.1f}% |",
            f"| Actual Deployment | {float(reasoning.get('actual_deployment_pct', 0)):.1f}% |",
            f"| Market Condition Score | {float(reasoning.get('market_condition_score', 0)):.2f} |",
            f"| Gap Explanation | {reasoning.get('gap_explanation', '-')} |",
            f"| Recommendation | {reasoning.get('recommendation', '-')} |",
            "",
            "**Risk Factors Limiting Deployment:**",
        ]
        lines += [f"- {factor}" for factor in risk_factors]

    comparison = sizing_result.get("deployment_scenario_comparison") or {}
    deploy_now = comparison.get("deploy_60_now") or {}
    wait = comparison.get("wait_for_confirmation") or {}
    if deploy_now or wait:
        lines += [
            "",
            "### Deploy 60% Now vs Wait",
            "",
            "| Scenario | Expected Return | Max Drawdown | Catatan |",
            "|---|---:|---:|---|",
            (
                f"| Deploy 60% sekarang | "
                f"{float(deploy_now.get('expected_return_portfolio_pct', 0)):.2f}% "
                f"({_rupiah(deploy_now.get('expected_return_rp'))}) | "
                f"{float(deploy_now.get('max_drawdown_portfolio_pct', 0)):.2f}% "
                f"({_rupiah(deploy_now.get('max_drawdown_rp'))}) | "
                f"Return on deployed {float(deploy_now.get('expected_return_on_deployed_pct', 0)):.2f}% |"
            ),
            (
                f"| Tunggu konfirmasi | "
                f"{float(wait.get('expected_return_portfolio_pct', 0)):.2f}% "
                f"({_rupiah(wait.get('expected_return_rp'))}) | "
                f"{float(wait.get('max_drawdown_portfolio_pct', 0)):.2f}% "
                f"({_rupiah(wait.get('max_drawdown_rp'))}) | "
                f"{wait.get('tradeoff', '-')} |"
            ),
        ]

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# ── ENTRY POINT ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

__all__ = ["_build_markdown_report", "_build_position_summary"]
