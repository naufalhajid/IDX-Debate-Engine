# ruff: noqa: E402

import argparse
import asyncio
import signal
import sys
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from builders.analysers import Analyser
from builders.database_builder import DatabaseBuilder
from db import database
from providers.idx import IDX
from providers.stockbit import StockBit
from schemas.builder import BuilderOutputType
from utils.logger_config import logger


def parse_arguments():
    parser = argparse.ArgumentParser(description="IDX Composite Fundamental Analysis")
    parser.add_argument(
        "-f",
        "--full-retrieve",
        action="store_true",
        help="Retrieve full stock data from IDX",
    )
    parser.add_argument(
        "-o",
        "--output-format",
        type=BuilderOutputType,
        choices=list(BuilderOutputType),
        default=BuilderOutputType.SPREADSHEET,
        help="Specify the output format: 'spreadsheet' for Google Spreadsheet, 'excel' for Excel file",
    )
    return parser.parse_args()


async def main_async() -> None:
    """Async entry point for ETL pipeline."""

    def _handle_shutdown(sig, frame):  # noqa: ANN001
        logger.warning(f"[main] Received signal {sig}. Shutting down ETL gracefully...")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_shutdown)
    signal.signal(signal.SIGTERM, _handle_shutdown)

    logger.info("IDX Composite Fundamental Analysis")
    start_time = time.time()

    args = parse_arguments()

    # Setup database
    await asyncio.to_thread(database.setup_db, is_drop_table=False)

    # Retrieve stocks from IDX
    idx = await asyncio.to_thread(IDX, is_full_retrieve=args.full_retrieve)
    stocks = await asyncio.to_thread(idx.stocks)
    logger.debug("Stocks: {}".format(stocks))
    logger.info("Total Stocks: {}".format(len(stocks)))

    # Process stocks key statistics, price, fundamental, and stream data (news) from Stockbit
    def _run_stockbit_pipeline() -> None:
        StockBit(stocks=stocks).with_stock_price().with_fundamental().with_stream_data()

    await asyncio.to_thread(_run_stockbit_pipeline)

    # Analyser to build the output
    title = f"IDX Fundamental Analysis {date.today().strftime('%Y-%m-%d')}"
    await Analyser(stocks=stocks).build(
        output=args.output_format,
        title=title,
    )

    # Populate to database
    database_builder = DatabaseBuilder(stocks=stocks)
    await database_builder.update_or_insert_stock()
    await database_builder.insert_key_statistic()
    await database_builder.insert_key_analysis()
    await database_builder.insert_stock_price()
    await database_builder.insert_sentiment()

    elapsed = time.time() - start_time
    elapsed_minutes = elapsed / 60
    logger.info(f"Elapsed time: {elapsed_minutes:.2f} minutes")


def main() -> None:
    """Sync wrapper - preserves CLI compatibility."""
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
