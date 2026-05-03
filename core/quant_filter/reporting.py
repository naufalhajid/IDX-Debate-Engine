"""Markdown reporting for the IHSG quantitative filter."""

from datetime import datetime

import pandas as pd

from utils.exdate_scanner import ExDateInfo, format_exdate_block

def _build_markdown_report(final_df: pd.DataFrame, cfg: dict) -> str:
    lines = []
    lines.append(f"# 🏆 Top {cfg['top_n']} High-Conviction IHSG Swing Candidates")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}*")
    lines.append("*Engine: v3.1 — Absolute scoring | Asymmetric RSI | Piotroski integrated | Bank-aware valuation*")
    lines.append("")
    lines.append(
        "| Rank | Ticker | Sektor | Harga | Stop Loss | Graham Fair Value "
        "| Score | Gap | RSI | PBV | F-Score | Entry Note |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")

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
            f"| {r['PBV']:.1f}× ({r['PBV vs Sektor']}) "
            f"| {piotroski_icon} {r.get('Piotroski F-Score', 'N/A')}/9 "
            f"| {r['Entry Strategy']}{ex_src} |"
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


# ══════════════════════════════════════════════════════════════════════════════
# ── ENTRY POINT ───────────────────────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════════════════════

__all__ = ["_build_markdown_report"]
