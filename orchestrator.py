"""
orchestrator.py — Automated Pipeline: Quant Scouting → Multi-Agent Debate → Top 3 Swing Trades.

Execution Pipeline:
  Step 1: Parse report.md from run_quant_filter.py, extract tickers, exclude critical risks.
  Step 2: Run DebateChamber.run(ticker) for each candidate sequentially.
  Step 3: Score & Rank using Conviction Score = 50% CIO Confidence + 50% R/R Ratio.
  Step 4: Persist full_batch_results.json + TOP_3_SWING_TRADES.md.
"""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf

from core.budget import BudgetExhaustedError, get_usage, reset_budget
from services.debate_chamber import DebateChamber
from schemas.debate import CIOVerdict
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price


# ---------------------------------------------------------------------------
# Concurrency controls — prevent the classic "10 tickers × 12 Pro calls
# fired in 2 seconds" rate-limit cascade that burns the daily budget.
# ---------------------------------------------------------------------------

#: Maximum number of debates that may be in-flight concurrently.  Gemini
#: Pro free tier is 2 RPM; even paid tiers get 429-heavy under bursty
#: concurrency.  3 gives a reasonable throughput floor without hammering.
MAX_CONCURRENT_DEBATES = 3


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JSON_PATH = Path("output/top10_candidates.json")
OUTPUT_DIR = Path("output")
FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"

# Conviction Score weights
W_CONFIDENCE = 0.50
W_RR_RATIO = 0.50

# R/R ratio normalization cap (prevents one extreme ratio from dominating)
RR_NORM_CAP = 5.0

# Ratings that are automatically excluded from Top 3
EXCLUDED_RATINGS = {"AVOID", "HOLD", "SELL"}

# Timezone for timestamps
WIB = timezone(timedelta(hours=7))


# ---------------------------------------------------------------------------
# Step 1: Automated Report Parsing
# ---------------------------------------------------------------------------

def parse_report(json_path: Path = JSON_PATH) -> list[str]:
    """
    Parse the structured top10_candidates.json file and extract candidate tickers.

    Ignores tickers with "Critical Risks" flags in their Entry Strategy note.
    Returns a deduplicated list of ticker strings (e.g. ["ERAA", "BUKA", ...]).
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"Candidates not found at {json_path}. Run run_quant_filter.py first."
        )

    content = json_path.read_text(encoding="utf-8")
    data = json.loads(content)
    tickers: list[str] = []

    for row in data:
        ticker = row.get("Ticker", "").strip().upper()
        if not ticker:
            continue
            
        strategy = row.get("Entry Strategy", "").lower()
        
        # Skip tickers flagged with critical risks
        if "critical risk" in strategy:
            logger.warning(f"[Parser] Skipping {ticker} — flagged with Critical Risks")
            continue

        if ticker not in tickers:
            tickers.append(ticker)

    logger.info(f"[Parser] Extracted {len(tickers)} tickers from JSON: {tickers}")
    return tickers


# ---------------------------------------------------------------------------
# Price Fetcher
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# Step 2: Batch Debate Runner
# ---------------------------------------------------------------------------

def _empty_result(ticker: str, error: str) -> dict:
    """Uniform shape for failed debates — keeps downstream code defensive."""

    return {
        "ticker": ticker,
        "verdict": {},
        "debate_rounds": 0,
        "debate_history": [],
        "raw_data_summary": "",
        "error": error,
    }


async def _run_single_debate(
    ticker: str, chamber: DebateChamber
) -> dict:
    """Run debate for a single ticker.

    Retries are handled inside ``DebateChamber._invoke_llm`` (tenacity
    with exponential backoff and permanent-error whitelist).  The
    previous nested ``max_retries=3`` loop here multiplied retries to
    a 9× worst case — removed to prevent budget drain.
    """

    logger.info(f"{'=' * 60}")
    logger.info(f"[Orchestrator] Starting debate for: {ticker}")
    logger.info(f"{'=' * 60}")

    # Fetch live price
    current_price = await fetch_current_price(ticker)
    if current_price == 0.0:
        logger.warning(
            f"[Orchestrator] Could not fetch price for {ticker} — "
            "trade levels will be degraded"
        )

    try:
        result = await chamber.run(ticker, current_price=current_price)
        if result.get("error") is not None:
            raise Exception(result["error"])

        verdict_dict = {}
        if result.get("final_verdict"):
            verdict_dict = json.loads(result["final_verdict"])

        logger.info(f"[Orchestrator] ✅ Debate complete for {ticker}")
        return {
            "ticker": result["ticker"],
            "verdict": verdict_dict,
            "debate_rounds": result["round_count"],
            "debate_history": [
                {"role": m.role, "content": m.content, "round": m.round_num}
                for m in result["debate_history"]
            ],
            "raw_data_summary": result["raw_data"],
            "error": None,
        }

    except BudgetExhaustedError as e:
        logger.error(f"[Orchestrator] 🛑 {ticker}: {e}")
        return _empty_result(ticker, f"Budget exhausted: {e}")
    except Exception as e:
        logger.error(f"[Orchestrator] 🚨 {ticker} debate failed: {e}")
        return _empty_result(ticker, str(e))


async def run_batch_debates(
    tickers: list[str]
) -> list[dict]:
    """
    Execute DebateChamber for all tickers with bounded concurrency.

    Concurrency is capped at ``MAX_CONCURRENT_DEBATES`` to avoid
    hammering Gemini's rate limit (which previously caused 429 storms
    and 3-deep retry fan-out that drained the daily budget).
    """

    logger.info(
        f"[Orchestrator] Launching {len(tickers)} debates "
        f"(concurrency={MAX_CONCURRENT_DEBATES})..."
    )

    chamber = DebateChamber()
    sem = asyncio.Semaphore(MAX_CONCURRENT_DEBATES)

    async def _guarded(ticker: str) -> dict:
        async with sem:
            try:
                return await _run_single_debate(ticker, chamber)
            except BudgetExhaustedError as e:
                logger.error(f"[Budget] Aborting remaining tickers: {e}")
                return _empty_result(ticker, f"Budget exhausted: {e}")
            except asyncio.CancelledError:
                # Should not reach here after debate_chamber fix, but kept
                # as a last-resort safety net so the batch continues.
                logger.error(f"[Orchestrator] ⚠️ {ticker}: CancelledError escaped — skipping ticker")
                return _empty_result(ticker, "CancelledError: request cancelled or timed out")
            except Exception as e:
                logger.error(f"[Orchestrator] 🚨 {ticker} unhandled exception in _guarded: {e}")
                return _empty_result(ticker, str(e))

    results = await asyncio.gather(
        *[_guarded(t) for t in tickers],
        return_exceptions=True,
    )

    # Convert any stray BaseException (should not happen after _guarded fix,
    # but this is the last-resort safety net) into empty result dicts.
    safe_results: list[dict] = []
    for ticker, res in zip(tickers, results):
        if isinstance(res, BaseException):
            logger.error(f"[Orchestrator] 🚨 {ticker} escaped all guards: {res}")
            safe_results.append(_empty_result(ticker, str(res)))
        else:
            safe_results.append(res)
    usage = get_usage()
    logger.info(
        f"[Budget] Run complete: "
        f"Pro {usage['pro_calls']}/{usage['pro_budget']}, "
        f"Flash {usage['flash_calls']}/{usage['flash_budget']}"
    )
    return safe_results


# ---------------------------------------------------------------------------
# Step 3: The "Top 3" Selection Algorithm
# ---------------------------------------------------------------------------

def compute_conviction_score(verdict: dict) -> tuple[float, str | None]:
    """
    Calculate Conviction Score:
      50% × CIO Confidence + 50% × Normalized R/R Score
    """
    confidence = float(verdict.get("confidence", 0.0) or 0.0)
    # Ensure confidence is scaled to [0, 1]
    if confidence > 1.0:
        confidence = confidence / 100.0
    confidence = max(0.0, min(confidence, 1.0))

    rr_ratio = float(verdict.get("risk_reward_ratio", 0.0) or 0.0)

    warning: str | None = None
    if rr_ratio > 5.0:
        # M3: R/R > 5× hampir selalu berarti stop loss terlalu sempit atau
        # target terlalu agresif — bukan trade yang realistis di IHSG
        warning = (
            f"⚠️ R/R {rr_ratio:.1f}× suspiciously high — "
            "verifikasi trade envelope: stop loss mungkin terlalu sempit "
            "atau target melebihi resistance kuat"
        )
    elif rr_ratio > 3.5:
        warning = f"⚠️ R/R {rr_ratio:.1f}× — verify stop is not inside noise band"

    rr_score = min(max(rr_ratio / RR_NORM_CAP, 0.0), 1.0)
    score = (W_CONFIDENCE * confidence) + (W_RR_RATIO * rr_score)
    return score, warning


def select_top3(results: list[dict]) -> list[dict]:
    """
    Rank all debate results by Conviction Score and return the Top 3.

    Exclusion Rule: Automatically reject AVOID, HOLD, and SELL ratings.
    """
    scorable: list[dict] = []

    for entry in results:
        verdict = entry.get("verdict", {})
        if not verdict:
            logger.info(f"[Rank] Skipping {entry['ticker']} — no verdict")
            continue

        rating = verdict.get("rating", "AVOID")
        if rating in EXCLUDED_RATINGS:
            logger.info(
                f"[Rank] Excluding {entry['ticker']} — rating is {rating}"
            )
            continue

        score, warning = compute_conviction_score(verdict)
        entry["conviction_score"] = round(score, 4)
        if warning:
            entry["rr_warning"] = warning
        scorable.append(entry)
        logger.info(
            f"[Rank] {entry['ticker']}: "
            f"confidence={verdict.get('confidence', 0):.2f}, "
            f"R/R={verdict.get('risk_reward_ratio', 0)}, "
            f"conviction={score:.4f}"
        )

    # Sort descending by conviction score
    scorable.sort(key=lambda x: x["conviction_score"], reverse=True)

    top3 = scorable[:3]
    logger.info(
        f"[Rank] Top 3: {[t['ticker'] for t in top3]} "
        f"(from {len(scorable)} eligible)"
    )
    return top3


# ---------------------------------------------------------------------------
# Step 4: Data Persistence & Final Reporting
# ---------------------------------------------------------------------------

def save_full_results(results: list[dict], path: Path = FULL_RESULTS_PATH) -> None:
    """Save all debate results as JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(f"[Persist] Full batch results saved → {path}")


def _extract_winning_argument(entry: dict) -> str:
    """Extract the Bull's strongest argument from the debate history."""
    bull_args = [
        h["content"]
        for h in entry.get("debate_history", [])
        if h.get("role") == "bull"
    ]
    if not bull_args:
        return "No bull argument recorded."

    # Return the last (most refined) bull argument, trimmed
    arg = bull_args[-1]
    # Truncate to ~500 chars for the executive summary
    if len(arg) > 500:
        arg = arg[:497] + "..."
    return arg


def _extract_devils_warning(entry: dict) -> str:
    """Extract the #1 risk from the Devil's Advocate."""
    da_args = [
        h["content"]
        for h in entry.get("debate_history", [])
        if h.get("role") == "devils_advocate"
    ]
    if not da_args:
        return "No devil's advocate challenge recorded."

    # Return the DA content, trimmed
    arg = da_args[-1]
    if len(arg) > 400:
        arg = arg[:397] + "..."
    return arg


def generate_top3_report(
    top3: list[dict],
    all_results: list[dict],
    path: Path = TOP3_REPORT_PATH,
) -> str:
    """
    Generate the final executive Markdown report for the Top 3 swing trades.
    Returns the Markdown string and saves it to disk.
    """
    timestamp = datetime.now(WIB).strftime("%Y-%m-%d %H:%M WIB")
    total_debated = len(all_results)
    eligible = len([
        r for r in all_results
        if r.get("verdict", {}).get("rating") not in EXCLUDED_RATINGS
        and r.get("verdict")
    ])

    lines: list[str] = [
        "# 🏆 TOP 3 HIGH-CONVICTION IHSG SWING TRADES",
        "",
        f"> **Generated**: {timestamp}",
        f"> **Pipeline**: Quant Scouting → Multi-Agent Debate → CIO Verdict",
        f"> **Stocks Debated**: {total_debated} | **Eligible (BUY/STRONG_BUY)**: {eligible} | **Selected**: {len(top3)}",
        "",
        "---",
        "",
    ]

    if not top3:
        lines.append("⚠️ **No stocks qualified for the Top 3.**")
        lines.append("")
        lines.append(
            "All candidates were rated HOLD, AVOID, or SELL by the CIO Judge. "
            "No high-conviction swing trades identified in this batch."
        )
        report_text = "\n".join(lines)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_text, encoding="utf-8")
        return report_text

    for rank, entry in enumerate(top3, 1):
        v = entry["verdict"]
        ticker = entry["ticker"]
        score = entry.get("conviction_score", 0)

        # Medal emoji
        medal = {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, "")

        lines.extend([
            f"## {medal} #{rank} — {ticker}",
            "",
            "### Final Rating & Confidence",
            "",
            f"| Metric | Value |",
            f"|---|---|",
            f"| **Rating** | `{v.get('rating', 'N/A')}` |",
            f"| **CIO Confidence** | {v.get('confidence', 0):.0%} |",
            f"| **Conviction Score** | {score:.2%} |",
            f"| **Timeframe** | {v.get('timeframe', '1-3 Months')} |",
            "",
            "### 📦 Trade Box",
            "",
            f"| Parameter | Level |",
            f"|---|---|",
            f"| **Buy Range** | Rp {v.get('entry_price_range', 'N/A')} |",
            f"| **Target Price** | Rp {v.get('target_price', 'N/A'):,.0f} |" if v.get('target_price') else f"| **Target Price** | N/A |",
            f"| **Stop Loss** | Rp {v.get('stop_loss', 'N/A'):,.0f} |" if v.get('stop_loss') else f"| **Stop Loss** | N/A |",
            f"| **Fair Value** | Rp {v.get('fair_value', 'N/A'):,.0f} |" if v.get('fair_value') else f"| **Fair Value** | N/A |",
            f"| **Expected Return** | {v.get('expected_return', 'N/A')} |",
            f"| **Risk/Reward** | {v.get('risk_reward_ratio', 'N/A')} |",
            "",
            "*All prices are IHSG tick-rounded and Python-computed.*",
            "",
            "### 🏆 Winning Argument",
            "",
            f"> {_extract_winning_argument(entry)}",
            "",
            "### ⚠️ Devil's Advocate Warning",
            "",
            f"> {_extract_devils_warning(entry)}",
            "",
            "### 💡 CIO Summary",
            "",
            v.get("summary", "No summary available."),
            "",
        ])

        if "rr_warning" in entry:
            lines.append(f"> **{entry['rr_warning']}**")
            lines.append("")

        # Key catalysts & risks
        catalysts = v.get("key_catalysts", [])
        risks = v.get("key_risks", [])

        if catalysts:
            lines.append("**Key Catalysts:**")
            for c in catalysts:
                lines.append(f"- {c}")
            lines.append("")

        if risks:
            lines.append("**Key Risks:**")
            for r in risks:
                lines.append(f"- {r}")
            lines.append("")

        lines.extend(["---", ""])

    # Footer with full batch summary table
    lines.extend([
        "## 📊 Full Batch Summary",
        "",
        "| Ticker | Rating | Confidence | R/R Ratio | Conviction Score | Status |",
        "|---|---|---|---|---|---|",
    ])

    # Include all results in the summary
    all_scored: list[dict] = []
    for entry in all_results:
        verdict = entry.get("verdict", {})
        if not verdict:
            all_scored.append({**entry, "conviction_score": 0.0})
            continue
        score, warning = compute_conviction_score(verdict)
        entry["conviction_score"] = round(score, 4)
        if warning:
            entry["rr_warning"] = warning
        all_scored.append(entry)

    all_scored.sort(key=lambda x: x["conviction_score"], reverse=True)

    top3_tickers = {t["ticker"] for t in top3}
    for entry in all_scored:
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "ERROR")
        conf = v.get("confidence", 0)
        rr = v.get("risk_reward_ratio", "N/A")
        cscore = entry.get("conviction_score", 0)

        if entry.get("error"):
            status = "❌ Error"
        elif ticker in top3_tickers:
            status = "🏆 Selected"
        elif rating in EXCLUDED_RATINGS:
            status = "⛔ Excluded"
        else:
            status = "—"

        rr_str = f"{rr:.2f}" if isinstance(rr, (int, float)) and rr else "N/A"
        lines.append(
            f"| {ticker} | {rating} | {conf:.0%} | {rr_str} | {cscore:.2%} | {status} |"
        )

    lines.extend([
        "",
        "---",
        f"*Report generated by `orchestrator.py` at {timestamp}*",
    ])

    report_text = "\n".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report_text, encoding="utf-8")
    logger.info(f"[Persist] Top 3 report saved → {path}")
    return report_text


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    """Full pipeline: Parse → Debate → Rank → Report."""
    logger.info("=" * 60)
    logger.info("[Orchestrator] 🚀 Starting IHSG Swing Trade Pipeline")
    logger.info("=" * 60)

    # Reset budget counters at the start of each run so repeated invocations
    # within the same interpreter session don't poison each other.
    reset_budget()

    # Step 1: Parse report
    tickers = parse_report()
    if not tickers:
        logger.error("[Orchestrator] No tickers found in report. Aborting.")
        return

    # Step 2: Run debates
    results = await run_batch_debates(tickers)

    # Step 3: Select Top 3
    top3 = select_top3(results)

    # Step 4: Save & Report
    save_full_results(results)
    report = generate_top3_report(top3, results)

    logger.info("=" * 60)
    logger.info("[Orchestrator] ✅ Pipeline Complete")
    logger.info(f"[Orchestrator] Full results → {FULL_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Top 3 report → {TOP3_REPORT_PATH}")
    logger.info("=" * 60)

    # Print the report to console
    print("\n" + report)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())