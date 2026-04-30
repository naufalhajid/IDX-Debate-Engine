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
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yfinance as yf
from pydantic import ValidationError
from tenacity import retry, stop_after_attempt, wait_exponential

from core.budget import BudgetExhaustedError, get_usage, reset_budget
from core.settings import settings
from services.debate_chamber import DebateChamber
from schemas.debate import CIOVerdict
from utils.logger_config import logger
from utils.price_fetcher import fetch_current_price


# ---------------------------------------------------------------------------
# Configuration - Centralized orchestrator settings
# ---------------------------------------------------------------------------

ORCHESTRATOR_CONFIG = {
    "conviction_weights": {"confidence": 0.50, "rr_ratio": 0.50},
    "rr_normalization_cap": 5.0,
    "max_concurrent_debates": int(os.getenv("MAX_CONCURRENT_DEBATES", "3")),
    "excluded_ratings": {"AVOID", "HOLD", "SELL"},
    "top_n_selection": int(os.getenv("TOP_N_SELECTION", "3")),
    "max_price_retry_attempts": int(os.getenv("MAX_PRICE_RETRY_ATTEMPTS", "3")),
}


# ---------------------------------------------------------------------------
# Concurrency controls — prevent the classic "10 tickers × 12 Pro calls
# fired in 2 seconds" rate-limit cascade that burns the daily budget.
# ---------------------------------------------------------------------------

#: Maximum number of debates that may be in-flight concurrently.  Gemini
#: Pro free tier is 2 RPM; even paid tiers get 429-heavy under bursty
#: concurrency.  3 gives a reasonable throughput floor without hammering.
MAX_CONCURRENT_DEBATES = ORCHESTRATOR_CONFIG["max_concurrent_debates"]


# ---------------------------------------------------------------------------
# Paths - Configurable via environment or settings
# ---------------------------------------------------------------------------

OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "output"))
JSON_PATH = OUTPUT_DIR / "top10_candidates.json"
FULL_RESULTS_PATH = OUTPUT_DIR / "full_batch_results.json"
TOP3_REPORT_PATH = OUTPUT_DIR / "TOP_3_SWING_TRADES.md"

# Conviction Score weights
W_CONFIDENCE = ORCHESTRATOR_CONFIG["conviction_weights"]["confidence"]
W_RR_RATIO = ORCHESTRATOR_CONFIG["conviction_weights"]["rr_ratio"]

# R/R ratio normalization cap (prevents one extreme ratio from dominating)
RR_NORM_CAP = ORCHESTRATOR_CONFIG["rr_normalization_cap"]

# Ratings that are automatically excluded from Top 3
EXCLUDED_RATINGS = ORCHESTRATOR_CONFIG["excluded_ratings"]

# Top N selection (configurable)
TOP_N_SELECTION = ORCHESTRATOR_CONFIG["top_n_selection"]


# ---------------------------------------------------------------------------
# Ticker Validation
# ---------------------------------------------------------------------------

TICKER_PATTERN = re.compile(r'^[A-Z]{4}(?:\.JK)?$')


def validate_ticker(ticker: str) -> bool:
    """
    Validate ticker format for IDX stocks.
    
    Accepts formats: "ERAA", "ERAA.JK" (case-insensitive, will be uppercased)
    Rejects: empty strings, special characters, invalid lengths
    
    Args:
        ticker: Raw ticker string to validate
        
    Returns:
        True if valid IDX ticker format, False otherwise
    """
    if not ticker or not isinstance(ticker, str):
        return False
    normalized = ticker.strip().upper()
    return bool(TICKER_PATTERN.match(normalized))


# ---------------------------------------------------------------------------
# Step 1: Automated Report Parsing
# ---------------------------------------------------------------------------

def parse_report(json_path: Path = JSON_PATH) -> list[str]:
    """
    Parse the structured top10_candidates.json file and extract candidate tickers.

    Ignores tickers with "Critical Risks" flags in their Entry Strategy note.
    Returns a deduplicated list of validated ticker strings (e.g. ["ERAA", "BUKA", ...]).
    
    Raises:
        FileNotFoundError: If JSON path doesn't exist
        ValueError: If no valid tickers found after filtering
    """
    if not json_path.exists():
        raise FileNotFoundError(
            f"Candidates not found at {json_path}. Run run_quant_filter.py first."
        )

    content = json_path.read_text(encoding="utf-8")
    data = json.loads(content)
    tickers: list[str] = []
    seen: set[str] = set()

    for row in data:
        ticker_raw = row.get("Ticker", "")
        ticker = ticker_raw.strip().upper() if ticker_raw else ""
        
        # Validate ticker format
        if not validate_ticker(ticker):
            logger.warning(f"[Parser] Invalid ticker format: '{ticker_raw}' — skipping")
            continue
            
        # Check for duplicates after normalization
        if ticker in seen:
            logger.debug(f"[Parser] Duplicate ticker: {ticker} — skipping")
            continue
            
        strategy = row.get("Entry Strategy", "").lower()
        
        # Skip tickers flagged with critical risks
        if "critical risk" in strategy:
            logger.warning(f"[Parser] Skipping {ticker} — flagged with Critical Risks")
            continue

        seen.add(ticker)
        tickers.append(ticker)

    if not tickers:
        raise ValueError("No valid tickers found after parsing and filtering")
        
    logger.info(f"[Parser] Extracted {len(tickers)} valid tickers from JSON: {tickers}")
    return tickers


# ---------------------------------------------------------------------------
# Price Fetcher with Retry Logic
# ---------------------------------------------------------------------------

@retry(
    stop=stop_after_attempt(ORCHESTRATOR_CONFIG["max_price_retry_attempts"]),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
async def fetch_price_with_retry(ticker: str) -> float:
    """
    Fetch current price with exponential backoff retry logic.
    
    Retries on transient failures (network issues, API rate limits) with
    exponential backoff: 2s, 4s, 8s (configurable attempts).
    
    Args:
        ticker: Stock ticker symbol
        
    Returns:
        Current price as float
        
    Raises:
        ValueError: If price fetch returns 0.0 after all retries
        Exception: Propagates other exceptions from fetch_current_price
    """
    price = await fetch_current_price(ticker)
    if price == 0.0:
        raise ValueError(f"Price fetch returned 0 for {ticker}")
    return price


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
    """
    Run debate for a single ticker with schema validation and retry logic.

    Features:
    - Price fetching with exponential backoff retry
    - CIOVerdict schema validation via Pydantic
    - Graceful degradation on transient failures
    
    Retries are handled inside ``DebateChamber._invoke_llm`` (tenacity
    with exponential backoff and permanent-error whitelist).  The
    previous nested ``max_retries=3`` loop here multiplied retries to
    a 9× worst case — removed to prevent budget drain.
    
    Args:
        ticker: Stock ticker symbol
        chamber: DebateChamber instance
        
    Returns:
        Dict with debate results or error information
    """
    logger.info("=" * 60)
    logger.info(f"[Orchestrator] Starting debate for: {ticker}", extra={"ticker": ticker})
    logger.info("=" * 60)

    # Fetch live price with retry logic
    current_price = 0.0
    try:
        current_price = await fetch_price_with_retry(ticker)
    except ValueError as e:
        logger.warning(f"[Orchestrator] Price fetch failed after retries for {ticker}: {e}")
        # Continue with degraded price (0.0) - debate can still run
    except Exception as e:
        logger.error(f"[Orchestrator] Unexpected price fetch error for {ticker}: {e}")
        # Continue with degraded price

    if current_price == 0.0:
        logger.warning(
            f"[Orchestrator] Could not fetch price for {ticker} — "
            "trade levels will be degraded",
            extra={"ticker": ticker},
        )

    try:
        result = await chamber.run(ticker, current_price=current_price)
        if result.get("error") is not None:
            raise Exception(result["error"])

        # Validate verdict schema using Pydantic
        verdict_dict = {}
        if result.get("final_verdict"):
            try:
                verdict_raw = json.loads(result["final_verdict"])
                # Validate against CIOVerdict schema
                verdict_obj = CIOVerdict(**verdict_raw)
                verdict_dict = verdict_obj.model_dump()
            except ValidationError as e:
                logger.error(
                    f"[Orchestrator] Invalid verdict schema for {ticker}: {e}",
                    extra={"ticker": ticker, "validation_error": str(e)},
                )
                return _empty_result(ticker, f"Schema validation failed: {e}")
            except json.JSONDecodeError as e:
                logger.error(
                    f"[Orchestrator] Malformed JSON verdict for {ticker}: {e}",
                    extra={"ticker": ticker},
                )
                return _empty_result(ticker, f"JSON decode error: {e}")

        logger.info(f"[Orchestrator] ✅ Debate complete for {ticker}", extra={"ticker": ticker})
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
        logger.error(f"[Orchestrator] 🛑 {ticker}: {e}", extra={"ticker": ticker})
        return _empty_result(ticker, f"Budget exhausted: {e}")
    except Exception as e:
        logger.error(f"[Orchestrator] 🚨 {ticker} debate failed: {e}", extra={"ticker": ticker})
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
    Rank all debate results by Conviction Score and return the Top N.

    Exclusion Rule: Automatically reject AVOID, HOLD, and SELL ratings.
    
    Tie Handling: Includes all tickers with conviction score equal to the
    cutoff threshold (e.g., if selecting top 3 and positions 3-5 have the
    same score, all three are included).
    
    Args:
        results: List of debate result dictionaries
        
    Returns:
        List of top N (or more in case of ties) debate results
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
        logger.debug(
            f"[Rank] {entry['ticker']}: "
            f"confidence={verdict.get('confidence', 0):.2f}, "
            f"R/R={verdict.get('risk_reward_ratio', 0)}, "
            f"conviction={score:.4f}"
        )

    # Sort descending by conviction score
    scorable.sort(key=lambda x: x["conviction_score"], reverse=True)

    # Select top N with tie handling
    if len(scorable) <= TOP_N_SELECTION:
        top_n = scorable
    else:
        top_n = scorable[:TOP_N_SELECTION]
        # Check for ties at the cutoff
        if TOP_N_SELECTION < len(scorable):
            tie_threshold = scorable[TOP_N_SELECTION - 1]["conviction_score"]
            for entry in scorable[TOP_N_SELECTION:]:
                if entry["conviction_score"] == tie_threshold:
                    top_n.append(entry)
                    logger.info(
                        f"[Rank] Including tie: {entry['ticker']} "
                        f"(score={tie_threshold:.4f})"
                    )

    logger.info(
        f"[Rank] Top {len(top_n)} selected: {[t['ticker'] for t in top_n]} "
        f"(from {len(scorable)} eligible, configured top_n={TOP_N_SELECTION})"
    )
    return top_n


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


def get_local_timestamp() -> str:
    """
    Get current timestamp in configured timezone (Asia/Jakarta by default).
    
    Uses UTC internally and converts to local timezone only for display,
    ensuring consistent behavior across different server environments.
    
    Returns:
        Formatted timestamp string with timezone abbreviation
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        # Fallback for Python < 3.9
        from backports.zoneinfo import ZoneInfo  # type: ignore
    
    utc_now = datetime.now(timezone.utc)
    local_tz = ZoneInfo(settings.DATETIME_TIMEZONE)  # Default: Asia/Jakarta
    return utc_now.astimezone(local_tz).strftime("%Y-%m-%d %H:%M %Z")


def generate_top3_report(
    top_n: list[dict],
    all_results: list[dict],
    path: Path = TOP3_REPORT_PATH,
) -> str:
    """
    Generate the final executive Markdown report for the Top N swing trades.
    Returns the Markdown string and saves it to disk.
    
    Args:
        top_n: List of top N selected debate results
        all_results: Full list of all debate results
        path: Output file path for the report
        
    Returns:
        Generated Markdown report as string
    """
    timestamp = get_local_timestamp()
    total_debated = len(all_results)
    selected_count = len(top_n)
    eligible = len([
        r for r in all_results
        if r.get("verdict", {}).get("rating") not in EXCLUDED_RATINGS
        and r.get("verdict")
    ])

    lines: list[str] = [
        f"# 🏆 TOP {selected_count} HIGH-CONVICTION IHSG SWING TRADES",
        "",
        f"> **Generated**: {timestamp}",
        f"> **Pipeline**: Quant Scouting → Multi-Agent Debate → CIO Verdict",
        f"> **Stocks Debated**: {total_debated} | **Eligible (BUY/STRONG_BUY)**: {eligible} | **Selected**: {selected_count}",
        "",
        "---",
        "",
    ]

    if not top_n:
        lines.append(f"⚠️ **No stocks qualified for the Top {TOP_N_SELECTION}.**")
        lines.append("")
        lines.append(
            "All candidates were rated HOLD, AVOID, or SELL by the CIO Judge. "
            "No high-conviction swing trades identified in this batch."
        )
        report_text = "\n".join(lines)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(report_text, encoding="utf-8")
        return report_text

    for rank, entry in enumerate(top_n, 1):
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

    selected_tickers = {t["ticker"] for t in top_n}
    for entry in all_scored:
        v = entry.get("verdict", {})
        ticker = entry["ticker"]
        rating = v.get("rating", "ERROR")
        conf = v.get("confidence", 0)
        rr = v.get("risk_reward_ratio", "N/A")
        cscore = entry.get("conviction_score", 0)

        if entry.get("error"):
            status = "❌ Error"
        elif ticker in selected_tickers:
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
    logger.info(f"[Persist] Top {len(top_n)} report saved → {path}")
    return report_text


# ---------------------------------------------------------------------------
# Main Entrypoint
# ---------------------------------------------------------------------------

async def main() -> None:
    """
    Full pipeline: Parse → Debate → Rank → Report.
    
    Orchestrates the complete IHSG swing trade analysis workflow:
    1. Parse candidate tickers from quant filter output
    2. Run multi-agent debate for each ticker
    3. Rank by conviction score and select top N
    4. Generate comprehensive markdown report
    """
    logger.info("=" * 60)
    logger.info("[Orchestrator] 🚀 Starting IHSG Swing Trade Pipeline")
    logger.info("=" * 60)

    # Reset budget counters at the start of each run so repeated invocations
    # within the same interpreter session don't poison each other.
    reset_budget()

    # Step 1: Parse report
    try:
        tickers = parse_report()
    except FileNotFoundError as e:
        logger.error(f"[Orchestrator] {e}")
        return
    except ValueError as e:
        logger.error(f"[Orchestrator] {e}")
        return
        
    if not tickers:
        logger.error("[Orchestrator] No valid tickers found after parsing. Aborting.")
        return

    # Step 2: Run debates
    results = await run_batch_debates(tickers)

    # Step 3: Select Top N (with tie handling)
    top_n = select_top3(results)

    # Step 4: Save & Report
    save_full_results(results)
    report = generate_top3_report(top_n, results)

    logger.info("=" * 60)
    logger.info("[Orchestrator] ✅ Pipeline Complete")
    logger.info(f"[Orchestrator] Full results → {FULL_RESULTS_PATH}")
    logger.info(f"[Orchestrator] Top {len(top_n)} report → {TOP3_REPORT_PATH}")
    logger.info("=" * 60)

    # Print the report to console
    print("\n" + report)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    asyncio.run(main())