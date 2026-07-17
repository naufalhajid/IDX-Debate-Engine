from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING, Any

from rich.table import Table
from rich.text import Text

from core.quant_filter.config import FINANCIAL_SECTORS

if TYPE_CHECKING:
    import pandas as pd


def build_sector_distribution_table(cache: dict[str, Any]) -> Table:
    """Build a compact sector distribution table from sector_cache.json payloads."""
    dist: Counter[str] = Counter()
    for value in cache.values():
        if isinstance(value, dict):
            sector = str(value.get("sector") or "default")
        else:
            sector = str(value or "default")
        dist[sector] += 1

    table = Table(title="Sector Cache")
    table.add_column("Sector", style="idx.header")
    table.add_column("Tickers", justify="right")
    for sector, count in sorted(dist.items(), key=lambda item: (-item[1], item[0])):
        table.add_row(sector, str(count))
    return table


def build_sector_members_table(
    sector: str,
    members: list[tuple[str, dict[str, Any] | str]],
) -> Table:
    table = Table(title=f"Sector: {sector}")
    table.add_column("Ticker", style="idx.ticker")
    table.add_column("Yahoo Sector")
    table.add_column("Yahoo Industry")
    for ticker, payload in members:
        if isinstance(payload, dict):
            table.add_row(
                ticker,
                str(payload.get("yf_sector") or "-"),
                str(payload.get("yf_industry") or "-"),
            )
        else:
            table.add_row(ticker, "-", "-")
    return table


def _safe_float(value: Any, default: float = 0.0) -> float | None:
    if value is None:
        return None
    try:
        v = float(value)
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def _risk_reason_codes(risk: dict[str, Any]) -> list[str]:
    raw = risk.get("reason_codes") if isinstance(risk, dict) else None
    if isinstance(raw, list):
        return [str(item) for item in raw if str(item)]
    return []


def _recommendation_context(item: dict[str, Any]) -> dict[str, Any]:
    direct = item.get("recommendation_context")
    if isinstance(direct, dict):
        return direct
    metadata = item.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    persisted = metadata.get("recommendation_context")
    if isinstance(persisted, dict):
        return persisted
    try:
        from services.recommendation_context import project_recommendation_context

        return project_recommendation_context(item)
    except Exception:
        return {}


def _execution_decision(item: dict[str, Any]) -> dict[str, Any]:
    decision = item.get("execution_decision")
    if isinstance(decision, dict):
        return decision
    return {
        "execution_status": item.get("execution_status"),
        "decision_source": item.get("decision_source"),
        "actionable": item.get("actionable"),
        "model_rating": item.get("model_rating"),
        "model_confidence": item.get("model_confidence"),
    }


def _metric_text(value: Any, unit: Any = None) -> str:
    if value is None:
        number = None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        if not math.isfinite(number):
            return "not recorded"
    unit_text = str(unit or "")
    if number is None:
        return "not recorded" if value in (None, "") else str(value)
    if unit_text == "IDR":
        return f"Rp{number:,.0f}"
    if unit_text == "x":
        return f"{number:.2f}x"
    if unit_text == "%":
        return f"{number:.2f}%"
    if unit_text == "bars":
        return f"{number:.0f} bars"
    return f"{number:.3g}{(' ' + unit_text) if unit_text else ''}"


def _primary_blocker(context: dict[str, Any]) -> dict[str, str]:
    blockers = context.get("blockers")
    blockers = blockers if isinstance(blockers, list) else []
    blocker = blockers[0] if blockers and isinstance(blockers[0], dict) else {}
    observations = blocker.get("observations")
    observations = observations if isinstance(observations, list) else []
    metric = observations[0] if observations and isinstance(observations[0], dict) else {}
    unit = metric.get("unit")
    normalized = _safe_float(metric.get("percentage_gap"))
    gap = _metric_text(metric.get("absolute_gap"), unit)
    if normalized is not None:
        gap = f"{gap} / {normalized:.1%}"
    return {
        "gate": str(blocker.get("gate_id") or "not recorded"),
        "observed": _metric_text(metric.get("observed"), unit),
        "required": (
            f"{metric.get('comparator') or ''} "
            f"{_metric_text(metric.get('threshold'), unit)}"
        ).strip(),
        "gap": gap,
        "trigger": str(
            blocker.get("next_observable_trigger")
            or context.get("next_observable_trigger")
            or "not recorded"
        ),
    }


def _execution_action_text(item: dict[str, Any]) -> Text:
    decision = _execution_decision(item)
    execution_status = str(decision.get("execution_status") or "").upper()
    context = _recommendation_context(item)
    recommendation_state = str(context.get("recommendation_state") or "")
    if execution_status == "EXECUTABLE_BUY" and decision.get("actionable") is True:
        return Text("EXECUTABLE / SIZING OK", style="idx.bull")
    if execution_status:
        suffix = f" / {recommendation_state}" if recommendation_state else ""
        return Text(f"{execution_status}{suffix} / NO SIZING", style="idx.avoid")

    risk = item.get("risk_governor") if isinstance(item, dict) else None
    if not isinstance(risk, dict):
        return Text("-", style="idx.muted")

    reason_codes = _risk_reason_codes(risk)
    status = str(risk.get("status") or "unknown")
    sizing_allowed = risk.get("sizing_allowed")

    if "market_regime_defensive" in reason_codes:
        return Text("No sizing: defensive market", style="amber")
    if status == "deployable" and sizing_allowed is not False:
        return Text("Ready", style="idx.bull")
    if status == "conditional_deployable":
        return Text("Conditional", style="amber")
    if status == "wait_for_pullback":
        return Text("Wait entry", style="amber")
    if status == "watchlist_only":
        return Text("Watchlist", style="idx.hold")
    if status == "reject":
        return Text("Rejected", style="idx.avoid")
    return Text(status.replace("_", " "), style="idx.muted")


def build_filter_results_table(df: "pd.DataFrame", top_n: int = 10) -> Table:
    """Rich table showing the top swing-trade candidates from the quant filter."""
    table = Table(
        title=f"Top {min(len(df), top_n)} Swing-Trade Candidates — IDX",
        show_header=True,
        header_style="idx.header",
        border_style="dim",
        show_lines=False,
        row_styles=["", "dim"],
    )
    table.add_column("#", style="dim", width=3, justify="right")
    table.add_column("Ticker", style="idx.ticker", min_width=6)
    table.add_column("Sector", style="idx.muted", min_width=12)
    table.add_column("Score", justify="right", min_width=6)
    table.add_column("Price", justify="right", min_width=9)
    table.add_column("Graham FV", justify="right", min_width=9)
    table.add_column("Upside%", justify="right", min_width=8)
    table.add_column("RSI", justify="right", width=6)
    table.add_column("Ex-Date", min_width=8)
    table.add_column("Strategy", max_width=42)
    table.add_column("F-Score", justify="right", width=7)

    for rank, (_, row) in enumerate(df.iterrows(), start=1):
        score = _safe_float(row.get("Composite Score")) or 0.0
        price = _safe_float(row.get("Current Price"))
        graham_raw = row.get("Est. Fair Value (Graham)")
        graham_fv = _safe_float(graham_raw) if graham_raw is not None else None
        upside = _safe_float(row.get("Valuation Gap (%)")) or 0.0
        rsi = _safe_float(row.get("RSI (14)")) or 0.0
        exdate_risk = str(row.get("ExDate Risk") or "CLEAR")
        strategy = str(row.get("Entry Strategy") or "")[:50]
        piotroski = int(_safe_float(row.get("Piotroski F-Score"), 0) or 0)
        sector_label = str(row.get("Sektor Key") or row.get("Sektor") or "")
        is_financial = row.get("Sektor Key") in FINANCIAL_SECTORS

        # Score
        if score >= 70:
            score_text = Text(f"{score:.1f}", style="idx.bull")
        elif score >= 50:
            score_text = Text(f"{score:.1f}", style="amber")
        else:
            score_text = Text(f"{score:.1f}", style="idx.bear")

        # Price
        price_text = f"Rp{price:,.0f}" if price else "—"

        # Graham FV — bank/finance_nonbank are scored by PBV-vs-benchmark, not
        # Graham, so showing a Graham number here would cite an unused methodology.
        if is_financial:
            graham_text = "PBV-based"
        elif graham_fv and graham_fv > 0:
            graham_text = f"Rp{graham_fv:,.0f}"
        else:
            graham_text = "—"

        # Upside% (always >= 0 — clipped in pipeline)
        if is_financial:
            upside_text = Text("N/A", style="idx.muted")
        elif upside >= 50:
            upside_text = Text(f"+{upside:.1f}%", style="idx.bull")
        elif upside >= 20:
            upside_text = Text(f"+{upside:.1f}%", style="bold green")
        elif upside >= 5:
            upside_text = Text(f"+{upside:.1f}%", style="green")
        else:
            upside_text = Text(f"+{upside:.1f}%", style="idx.muted")

        # RSI
        if rsi < 45:
            rsi_text = Text(f"{rsi:.1f}", style="bold cyan")
        elif rsi > 70:
            rsi_text = Text(f"{rsi:.1f}", style="amber")
        else:
            rsi_text = Text(f"{rsi:.1f}", style="idx.value")

        # ExDate (CRITICAL tickers are rejected upstream — only CLEAR/WARNING here)
        if exdate_risk == "WARNING":
            exdate_text = Text("WARNING", style="amber")
        else:
            exdate_text = Text("CLEAR", style="idx.muted")

        # F-Score
        if piotroski >= 7:
            fscore_text = Text(f"{piotroski}/9", style="idx.bull")
        elif piotroski <= 3:
            fscore_text = Text(f"{piotroski}/9", style="idx.bear")
        else:
            fscore_text = Text(f"{piotroski}/9", style="idx.value")

        table.add_row(
            str(rank),
            str(row.get("Ticker", "")),
            sector_label,
            score_text,
            price_text,
            graham_text,
            upside_text,
            rsi_text,
            exdate_text,
            strategy,
            fscore_text,
        )

    return table


def build_verdict_summary_table(results: list[dict]) -> Table:
    """Compact post-debate / post-pipeline verdict summary table."""
    table = Table(
        title="Verdict Summary",
        show_header=True,
        header_style="idx.header",
        border_style="dim",
        show_lines=False,
        row_styles=["", "dim"],
    )
    table.add_column("Ticker", style="idx.ticker", min_width=6)
    table.add_column("Model", min_width=8)
    table.add_column("Action", min_width=16, max_width=28)
    table.add_column("Rec State", min_width=16, max_width=22)
    table.add_column("Conf%", justify="right", width=6)
    table.add_column("R/R", justify="right", width=5)
    table.add_column("Entry", min_width=14)
    table.add_column("Target", justify="right", min_width=9)
    table.add_column("Stop", justify="right", min_width=9)
    table.add_column("Return%", justify="right", width=8)
    table.add_column("Rounds", justify="right", width=7)

    for item in results:
        verdict = item.get("verdict") or {}
        ticker = str(item.get("ticker") or verdict.get("ticker") or "—")
        decision = _execution_decision(item)
        context = _recommendation_context(item)
        decision_source = str(decision.get("decision_source") or "").lower()
        model_rating = decision.get("model_rating")
        if model_rating is None and decision_source == "preflight":
            rating = "NOT_EVALUATED"
            confidence = None
        else:
            rating = str(model_rating or verdict.get("rating") or "—")
            confidence = _safe_float(
                decision.get("model_confidence")
                if decision.get("model_confidence") is not None
                else verdict.get("confidence")
            )
        rr = _safe_float(verdict.get("risk_reward_ratio"))
        entry = str(verdict.get("entry_price_range") or "—")
        target_raw = verdict.get("target_price")
        stop_raw = verdict.get("stop_loss")
        expected_return = str(verdict.get("expected_return") or "—")
        rounds = int(_safe_float(item.get("debate_rounds"), 0) or 0)

        # Rating style
        rating_upper = rating.upper()
        if rating_upper == "NOT_EVALUATED":
            rating_text = Text(rating, style="idx.muted")
        elif "BUY" in rating_upper:
            rating_text = Text(rating, style="idx.buy")
        elif "HOLD" in rating_upper:
            rating_text = Text(rating, style="idx.hold")
        else:
            rating_text = Text(rating, style="idx.avoid")

        # Confidence
        conf_pct = confidence * 100 if confidence is not None else None
        if conf_pct is None:
            conf_text = Text("N/A", style="idx.muted")
        elif conf_pct >= 70:
            conf_text = Text(f"{conf_pct:.0f}%", style="idx.bull")
        elif conf_pct >= 50:
            conf_text = Text(f"{conf_pct:.0f}%", style="amber")
        else:
            conf_text = Text(f"{conf_pct:.0f}%", style="idx.bear")

        # R/R
        if rr is None:
            rr_text = Text("N/A", style="idx.muted")
        elif rr >= 2.0:
            rr_text = Text(f"{rr:.2f}", style="idx.bull")
        elif rr >= 1.5:
            rr_text = Text(f"{rr:.2f}", style="amber")
        else:
            rr_text = Text(f"{rr:.2f}", style="idx.bear")

        # Target / Stop
        try:
            target_str = (
                f"Rp{float(target_raw):,.0f}" if target_raw is not None else "-"
            )
        except (TypeError, ValueError):
            target_str = "-"
        try:
            stop_str = f"Rp{float(stop_raw):,.0f}" if stop_raw is not None else "-"
        except (TypeError, ValueError):
            stop_str = "-"

        table.add_row(
            ticker,
            rating_text,
            _execution_action_text(item),
            str(context.get("recommendation_state") or "UNCLASSIFIED"),
            conf_text,
            rr_text,
            entry,
            target_str,
            stop_str,
            expected_return,
            str(rounds),
        )

    return table


def build_recommendation_diagnostics_tables(
    results: list[dict],
) -> list[Table]:
    """Build state-separated display tables; none of them grants sizing."""

    groups: dict[str, tuple[str, list[dict[str, Any]]]] = {
        "WAIT_TRIGGER": ("Wait Trigger — NO SIZING", []),
        "NEAR_MISS": ("Near Miss (presentation only) — NO SIZING", []),
        "REJECTED": ("Rejected Setups — NO SIZING", []),
        "DATA_INSUFFICIENT": ("Data Insufficient / Abstain — NO SIZING", []),
    }
    for item in results:
        context = _recommendation_context(item)
        state = str(context.get("recommendation_state") or "").upper()
        if state in {"SINGLE_GATE_REJECT", "HARD_REJECT"}:
            groups["REJECTED"][1].append(item)
        elif state in groups:
            groups[state][1].append(item)

    tables: list[Table] = []
    for _group, (title, rows) in groups.items():
        if not rows:
            continue
        table = Table(
            title=title,
            show_header=True,
            header_style="idx.header",
            border_style="dim",
            show_lines=False,
        )
        table.add_column("Ticker", style="idx.ticker", no_wrap=True)
        table.add_column("Execution", no_wrap=True)
        table.add_column("State", no_wrap=True)
        table.add_column("Gate")
        table.add_column("Observed")
        table.add_column("Required")
        table.add_column("Gap")
        table.add_column("Next Trigger", max_width=60)
        table.add_column("Sizing", style="idx.avoid", no_wrap=True)
        for item in rows:
            context = _recommendation_context(item)
            decision = _execution_decision(item)
            blocker = _primary_blocker(context)
            table.add_row(
                str(item.get("ticker") or "—"),
                str(decision.get("execution_status") or "UNCLASSIFIED"),
                str(context.get("recommendation_state") or "UNCLASSIFIED"),
                blocker["gate"],
                blocker["observed"],
                blocker["required"],
                blocker["gap"],
                blocker["trigger"],
                "NO",
            )
        tables.append(table)
    return tables
