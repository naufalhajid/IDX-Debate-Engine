import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv

from services.debate_chamber import DebateChamber
from utils.logger_config import logger

load_dotenv()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock Debate Chamber — Adversarial Multi-Agent Analysis",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        required=True,
        help="List of stock tickers to debate (e.g., BBRI BBCA TLKM)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="output/debates",
        help="Directory to save debate reports (default: output/debates)",
    )
    return parser.parse_args()


async def _debate_one(ticker: str, chamber: DebateChamber, output_dir: Path) -> bool:
    """
    Run the full debate pipeline for a single ticker and save the result.

    Returns True on success, False on any failure (so the caller can tally
    how many tickers were processed correctly).

    All exceptions — including asyncio.CancelledError from dropped Gemini
    connections — are caught here so the outer loop always continues to the
    next ticker rather than aborting the entire run.
    """
    logger.info(f"{'=' * 60}")
    logger.info(f"Starting debate for: {ticker}")
    logger.info(f"{'=' * 60}")

    try:
        result = await chamber.run(ticker)

        if result.get("error") is not None:
            logger.error(f"Debate aborted for {ticker}: {result['error']}")
            return False

        # Build report dict
        report = {
            "ticker": result["ticker"],
            "verdict": json.loads(result["final_verdict"]) if result["final_verdict"] else {},
            "debate_rounds": result["round_count"],
            "debate_history": [
                {"role": m.role, "content": m.content, "round": m.round_num}
                for m in result["debate_history"]
            ],
            "raw_data_summary": result["raw_data"],
            "metadata": result.get("metadata", {}),
        }

        report_path = output_dir / f"{ticker}_debate.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        logger.info(f"Report saved to {report_path}")
        return True

    except asyncio.CancelledError:
        # Gemini connection dropped / timed out at the httpx layer.
        # debate_chamber wraps this in RuntimeError for tenacity, but in case
        # it ever escapes, catch it here so the loop continues.
        logger.error(
            f"[run_debate] ⚠️  {ticker}: CancelledError — connection dropped or timed out. "
            "Skipping to next ticker."
        )
        return False
    except Exception as e:
        logger.error(f"[run_debate] 🚨 {ticker} failed unexpectedly: {e}")
        return False


async def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # LLM instances created once and reused for all tickers
    chamber = DebateChamber()

    succeeded, failed = 0, 0
    for ticker in args.tickers:
        ok = await _debate_one(ticker, chamber, output_dir)
        if ok:
            succeeded += 1
        else:
            failed += 1

    logger.info(
        f"All debates complete. ✅ {succeeded} succeeded / ❌ {failed} failed "
        f"out of {len(args.tickers)} tickers."
    )


if __name__ == "__main__":
    asyncio.run(main())
